# File device_list.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Device view supporting list and icon modes."""

import gi
import hashlib
import ipaddress
import math
import json
import logging
import re
import os
from datetime import datetime, timezone
import ssl
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from urllib.request import urlopen

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango
from collections.abc import Callable

from model.device import Device
from ui.device_details import DeviceDetailsDialog
from ui.icons import iter_icon_picker_entries, resolve_bundled_freedesktop_icon, resolve_icon_path
from utils.type_icon_config import type_icon_basenames_for_slug
from utils.location_label import is_plausible_room_location
from utils.discovery_config import (
    information_precedence_rank,
    information_precedence_role_for_device_source,
    normalize_information_precedence_list,
)
from utils.discovery_identity import uuid_urn_if_present
from utils.neighbor_mac import lookup_mac_from_neighbor_cache
from utils.gtk_dialog import prepare_gtk_dialog
from utils.connect_launcher import launch_connect_for_uri
from utils.custom_command import build_argv_from_template, spawn_custom_command_detached
from utils.scheduling import gtk_idle_schedule
from utils.double_click_open import resolve_connect_target, resolve_all_connect_targets
from utils.details_payload import (
    aggregate_ports_display,
    build_mdns_payload,
    build_ssdp_payload,
    build_wsd_family_detail_fields,
    collect_link_local_ipv6,
    dedupe_last_seen_in_fields,
    format_device_ip_for_details,
    format_device_type_for_details,
    merge_ssdp_mdns_detail_fields,
    strip_detail_fields_covered_by_overview,
    with_aggregate_ports_field,
)

_logger = logging.getLogger(__name__)


# When ``information_precedence`` ties two protocol rows, prefer this order (lower first).
_SOURCE_PRIMARY_TIEBREAK = {"ssdp": 0, "wsdd": 1, "wsd": 2, "nmb": 3, "mdns": 4}
# Allow a slightly lower-precedence row if it carries a real hostname (NetBIOS / mDNS) vs generic WSD.
_PRIMARY_RANK_SLACK_FOR_NAME = 2

# Scope-style / UUID-tail labels: ``WSD-45982f41``, ``ws-45982f41``, ``WSD ·deadbeef`` (not hostnames).
_WSD_SYNTHETIC_DISPLAY_RE = re.compile(
    r"^ws[d]?[\s\-·∙]+[0-9a-f]{6,}$",
    re.IGNORECASE,
)


def _weak_bundle_display_name(device: Device | None) -> bool:
    """True if the row label is too generic to prefer over another protocol's hostname."""
    if device is None:
        return True
    n = (getattr(device, "name", None) or "").strip()
    if not n:
        return True
    low = n.lower()
    if low == "wsd host":
        return True
    if low.startswith("wsd ·") or low.startswith("wsd \u00b7"):
        return True
    if _WSD_SYNTHETIC_DISPLAY_RE.match(n.strip()):
        return True
    if len(n) <= 1:
        return True
    try:
        ipaddress.ip_address(n.strip("[]"))
        return True
    except ValueError:
        pass
    return False


def _bundle_row_debug_line(
    dev: Device,
    *,
    rank: int,
    role: str,
    tie: int,
    weak: bool,
) -> str:
    return (
        f"{dev.source}@{dev.ip}:{dev.port} name={dev.name!r} role={role} "
        f"precedence_index={rank} tiebreak={tie} weak_name={weak}"
    )


@dataclass(slots=True)
class _DeviceBundle:
    ip: str
    port: int
    primary: Device
    ssdp_device: Device | None = None
    mdns_device: Device | None = None
    wsd_device: Device | None = None
    wsdd_device: Device | None = None
    nmb_device: Device | None = None

    @property
    def name(self) -> str:
        return self.primary.name

    @property
    def category(self) -> str:
        return self.primary.category

    @property
    def type(self) -> str:
        return self.primary.type

    @property
    def icon(self) -> str | None:
        return self.primary.icon

    @property
    def online(self) -> bool:
        return any(device.online for device in self.devices)

    @property
    def monitored(self) -> bool:
        return any(getattr(device, "monitored", False) for device in self.devices)

    @property
    def badges(self) -> list[tuple[str, Gtk.Align]]:
        return []

    @property
    def devices(self) -> list[Device]:
        unique: dict[str, Device] = {}
        for device in (self.mdns_device, self.ssdp_device, self.wsdd_device, self.wsd_device, self.nmb_device, self.primary):
            if device is not None:
                unique[device.key] = device
        return list(unique.values())


def _bundle_identity_provisional(bundle: _DeviceBundle) -> bool:
    """True when the list row still uses a WSD scope-style name on IPv6 (identity not final)."""
    if not _weak_bundle_display_name(bundle.primary):
        return False
    raw = (bundle.ip or "").strip().split("%", 1)[0]
    if not raw:
        return False
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return isinstance(addr, ipaddress.IPv6Address)


class DeviceList(Gtk.Box):
    def __init__(
        self,
        parent_window: Gtk.Window | None = None,
        on_set_monitored: Callable[[Device, bool], None] | None = None,
        on_icon_mode_changed: Callable[[], None] | None = None,
        on_set_type_override: Callable[[str, str, int, str | None], None] | None = None,
        on_set_name_override: Callable[[str, str, int, str | None], None] | None = None,
        on_set_location_override: Callable[[str, str, int, str | None], None] | None = None,
        on_set_url_override: Callable[[str, str, int, str | None], None] | None = None,
        on_set_device_commands: Callable[[str, str, int, list], None] | None = None,
        on_set_custom_command: Callable[[str, str, int, str | None], None] | None = None,
        on_set_field_rule: Callable[[str, str, int, str, str], None] | None = None,
        on_remove_field_rule: Callable[[str, str, int, str, str | None], None] | None = None,
        on_get_field_rules: Callable[[str, str, int], dict[str, list[str]]] | None = None,
        information_precedence: list[str] | None = None,
        show_ip_in_device_list: bool = True,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._current_view = "icons"
        self._icon_sort_mode = "sorted"
        self._devices: list[Device] = []
        self._filtered_devices: list[_DeviceBundle] = []
        self._category_filter: str | None = None
        self._parent_window = parent_window
        self._on_set_monitored = on_set_monitored
        self._on_icon_mode_changed = on_icon_mode_changed
        self._on_set_type_override = on_set_type_override
        self._on_set_name_override = on_set_name_override
        self._on_set_location_override = on_set_location_override
        self._on_set_url_override = on_set_url_override
        self._on_set_device_commands = on_set_device_commands
        self._on_set_custom_command = on_set_custom_command
        self._on_set_field_rule = on_set_field_rule
        self._on_remove_field_rule = on_remove_field_rule
        self._on_get_field_rules = on_get_field_rules
        self._information_precedence: list[str] = (
            list(information_precedence) if information_precedence else normalize_information_precedence_list(None)
        )
        self._show_ip_in_device_list = bool(show_ip_in_device_list)
        self._location_options: list[str] = []
        self._type_options: list[tuple[str, str]] = []  # (translated_label, slug)
        self._icon_source_overrides: dict[tuple[str, int], str] = {}
        self._custom_icon_overrides: dict[tuple[str, int], str] = {}
        self._remote_icon_cache: dict[str, GdkPixbuf.Pixbuf] = {}
        self._remote_icon_by_endpoint: dict[tuple[str, int], GdkPixbuf.Pixbuf] = {}
        self._remote_icon_by_host_ip: dict[str, GdkPixbuf.Pixbuf] = {}
        self._remote_icon_fetching: set[str] = set()
        self._remote_icon_disk_dir = Path.home() / ".cache" / "netneighbor" / "remote_icons"
        self._remote_icon_index_path = Path.home() / ".cache" / "netneighbor" / "remote_icon_index.json"
        self._remote_icon_index: dict[str, dict[str, str]] = {}
        self._remote_icon_index_loaded = False
        self._custom_icons_dir = Path.home() / ".config" / "netneighbor" / "custom_icons"
        self._builtin_icons_dir = Path(__file__).resolve().parent.parent / "assets" / "icons"
        self._custom_command_template = ""
        self._connect_command_templates: dict[str, str] = {}
        self._install_css()

        self._list_store = Gtk.ListStore(object, str, str, str, str)
        self._tree = Gtk.TreeView(model=self._list_store)
        self._offline_fg = Gdk.RGBA(0.45, 0.45, 0.45, 1.0)
        self._provisional_fg = Gdk.RGBA(0.5, 0.5, 0.48, 1.0)
        self._provisional_pulse_phase = 0.0
        self._add_column("Name", 1)
        self._add_column("IP", 2)
        self._add_column(_("Ports"), 3)
        self._add_column(_("Type"), 4)
        self._tree.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self._tree.connect("button-press-event", self._on_tree_button_press)
        self._tree.connect("row-activated", self._on_tree_row_activated)
        self._tree_scroll = Gtk.ScrolledWindow()
        self._tree_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._tree_scroll.add(self._tree)

        self._icon_sections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._icon_sections.set_margin_start(12)
        self._icon_sections.set_margin_end(6)
        self._icon_sections.set_margin_top(4)
        self._icon_scroll = Gtk.ScrolledWindow()
        self._icon_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._icon_scroll.add(self._icon_sections)

        self._stack = Gtk.Stack()
        self._stack.add_named(self._icon_scroll, "icons")
        self._stack.add_named(self._tree_scroll, "list")
        self._stack.set_visible_child_name(self._current_view)

        self.pack_start(self._stack, True, True, 0)
        self.show_all()
        GLib.timeout_add(420, self._list_pulse_provisional_identity_tick)

    def set_custom_command_template(self, template: str) -> None:
        self._custom_command_template = str(template or "")

    def set_connect_command_templates(self, templates: dict[str, str]) -> None:
        self._connect_command_templates = dict(templates) if templates else {}

    def build_bundles(self, devices: list[Device]) -> list[_DeviceBundle]:
        """Merge SSDP/mDNS rows by host keys; same logic as the icon/list view."""
        return self._build_device_bundles(devices)

    def bundle_location_label(self, bundle: _DeviceBundle) -> str:
        return self._bundle_location_label(bundle)

    def set_devices(self, devices: list[Device]) -> None:
        self._devices = devices
        bundles = self.build_bundles(devices)
        self._filtered_devices = self._apply_category_filter(bundles)
        self._list_store.clear()
        for bundle in self._filtered_devices:
            ip_col = (
                format_device_ip_for_details(bundle.primary) if self._show_ip_in_device_list else ""
            )
            self._list_store.append(
                [
                    bundle,
                    bundle.name,
                    ip_col,
                    aggregate_ports_display(
                        bundle.ssdp_device,
                        bundle.wsdd_device,
                        bundle.wsd_device,
                        bundle.nmb_device,
                        bundle.mdns_device,
                    ),
                    format_device_type_for_details(bundle.primary),
                ]
            )
        self._rebuild_icon_sections(self._filtered_devices)

    def set_category_filter(self, category: str | None) -> None:
        self._category_filter = category
        self.set_devices(self._devices)

    def set_view_mode(self, view_mode: str) -> None:
        if view_mode not in {"list", "icons"}:
            return
        self._current_view = view_mode
        self._stack.set_visible_child_name(view_mode)

    def set_icon_sort_mode(self, mode: str) -> None:
        if mode not in {"sorted", "appearance", "location"}:
            return
        self._icon_sort_mode = mode
        self._rebuild_icon_sections(self._filtered_devices)

    def set_type_options(self, options: list[tuple[str, str]]) -> None:
        """Set the type choices shown in the right-click 'Device type' submenu.

        Each entry is a ``(translated_label, slug)`` pair.  The *Auto* entry is
        always prepended automatically; callers should not include it.
        """
        seen_slugs: set[str] = set()
        normalized: list[tuple[str, str]] = []
        for item in options:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            label = str(item[0]).strip()
            slug = str(item[1]).strip().lower()
            if not label or not slug:
                continue
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            normalized.append((label, slug))
        self._type_options = normalized

    def set_location_options(self, options: list[str]) -> None:
        normalized: list[str] = []
        for value in options:
            if not isinstance(value, str):
                continue
            text = value.strip()
            if text and text not in normalized:
                normalized.append(text)
        self._location_options = normalized

    def set_icon_source_overrides(self, overrides: dict) -> None:
        normalized: dict[tuple[str, int], str] = {}
        for key, mode in overrides.items():
            if mode not in {"auto", "provided", "system", "custom"}:
                continue
            if not isinstance(key, str) or ":" not in key:
                continue
            ip, port_text = key.rsplit(":", 1)
            try:
                port = int(port_text)
            except ValueError:
                continue
            normalized[(ip, port)] = mode
        self._icon_source_overrides = normalized
        self._rebuild_icon_sections(self._filtered_devices)

    def get_icon_source_overrides(self) -> dict[str, str]:
        return {f"{ip}:{port}": mode for (ip, port), mode in self._icon_source_overrides.items()}

    def set_custom_icon_overrides(self, overrides: dict) -> None:
        normalized: dict[tuple[str, int], str] = {}
        for key, icon_name in overrides.items():
            if not isinstance(icon_name, str) or not icon_name.strip():
                continue
            if not isinstance(key, str) or ":" not in key:
                continue
            ip, port_text = key.rsplit(":", 1)
            try:
                port = int(port_text)
            except ValueError:
                continue
            normalized[(ip, port)] = icon_name.strip()
        self._custom_icon_overrides = normalized
        self._rebuild_icon_sections(self._filtered_devices)

    def get_custom_icon_overrides(self) -> dict[str, str]:
        return {f"{ip}:{port}": icon for (ip, port), icon in self._custom_icon_overrides.items()}

    @property
    def view_mode(self) -> str:
        return self._current_view

    @property
    def icon_sort_mode(self) -> str:
        return self._icon_sort_mode

    def _rebuild_icon_sections(self, bundles: list[_DeviceBundle]) -> None:
        for child in self._icon_sections.get_children():
            self._icon_sections.remove(child)

        if self._icon_sort_mode == "appearance":
            ordered = sorted(bundles, key=self._bundle_arrival_index)
            self._rebuild_flat_icon_panel(ordered)
            self._icon_sections.show_all()
            return
        if self._icon_sort_mode == "location":
            self._rebuild_icons_by_location(bundles)
            self._icon_sections.show_all()
            return

        grouped_devices: dict[str, list[_DeviceBundle]] = defaultdict(list)
        for bundle in bundles:
            grouped_devices[format_device_type_for_details(bundle.primary)].append(bundle)

        for category in sorted(grouped_devices.keys(), key=str.lower):
            frame = Gtk.Frame()
            frame.set_shadow_type(Gtk.ShadowType.IN)
            frame_label = Gtk.Label(label=category, xalign=0.0)
            frame_label.set_margin_start(8)
            frame.set_label_widget(frame_label)
            frame_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            frame_content.set_border_width(8)
            frame_content.set_margin_start(6)
            frame.add(frame_content)

            flow = Gtk.FlowBox()
            flow.set_max_children_per_line(6)
            flow.set_selection_mode(Gtk.SelectionMode.NONE)
            flow.set_row_spacing(10)
            flow.set_column_spacing(10)
            flow.set_homogeneous(False)

            for bundle in sorted(grouped_devices[category], key=lambda item: item.name.lower()):
                flow.add(self._build_icon_tile(bundle))

            frame_content.pack_start(flow, False, False, 0)
            self._icon_sections.pack_start(frame, False, False, 0)

        self._icon_sections.show_all()

    def _rebuild_flat_icon_panel(self, bundles: list[_DeviceBundle]) -> None:
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame_label = Gtk.Label(label=_("All devices"), xalign=0.0)
        frame_label.set_margin_start(8)
        frame.set_label_widget(frame_label)
        frame_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        frame_content.set_border_width(8)
        frame_content.set_margin_start(6)
        frame.add(frame_content)

        flow = Gtk.FlowBox()
        flow.set_max_children_per_line(6)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_row_spacing(10)
        flow.set_column_spacing(10)
        flow.set_homogeneous(False)
        for bundle in bundles:
            flow.add(self._build_icon_tile(bundle))
        frame_content.pack_start(flow, False, False, 0)
        self._icon_sections.pack_start(frame, False, False, 0)

    def _rebuild_icons_by_location(self, bundles: list[_DeviceBundle]) -> None:
        grouped_devices: dict[str, list[_DeviceBundle]] = defaultdict(list)
        for bundle in bundles:
            grouped_devices[self._bundle_location_label(bundle)].append(bundle)

        for location in sorted([label for label in grouped_devices.keys() if label != _("No location")], key=str.lower):
            self._add_location_section(location, grouped_devices[location])
        if _("No location") in grouped_devices:
            self._add_location_section(_("No location"), grouped_devices[_("No location")])

    def _add_location_section(self, location: str, bundles: list[_DeviceBundle]) -> None:
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame_label = Gtk.Label(label=location, xalign=0.0)
        frame_label.set_margin_start(8)
        frame.set_label_widget(frame_label)
        frame_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        frame_content.set_border_width(8)
        frame_content.set_margin_start(6)
        frame.add(frame_content)
        flow = Gtk.FlowBox()
        flow.set_max_children_per_line(6)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_row_spacing(10)
        flow.set_column_spacing(10)
        flow.set_homogeneous(False)
        for bundle in sorted(bundles, key=lambda item: item.name.lower()):
            flow.add(self._build_icon_tile(bundle))
        frame_content.pack_start(flow, False, False, 0)
        self._icon_sections.pack_start(frame, False, False, 0)

    def _bundle_location_label(self, bundle: _DeviceBundle) -> str:
        for device in bundle.devices:
            metadata = device.metadata if isinstance(device.metadata, dict) else {}
            location = metadata.get("user_location")
            if isinstance(location, str) and location.strip():
                s = location.strip()
                if is_plausible_room_location(s):
                    return s
        return _("No location")

    def _bundle_arrival_index(self, bundle: _DeviceBundle) -> int:
        indexes: list[int] = []
        for device in bundle.devices:
            metadata = device.metadata if isinstance(device.metadata, dict) else {}
            raw = metadata.get("_arrival_index")
            if isinstance(raw, int):
                indexes.append(raw)
        if indexes:
            return min(indexes)
        return 10**12

    def _build_icon_tile(self, bundle: _DeviceBundle) -> Gtk.Widget:
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tile.set_size_request(130, -1)

        icon_overlay = Gtk.Overlay()
        icon_overlay.set_size_request(-1, 64)
        icon_overlay.set_halign(Gtk.Align.CENTER)
        icon_overlay.set_valign(Gtk.Align.CENTER)
        image = Gtk.Image.new_from_pixbuf(self._load_device_icon(bundle))
        image.set_halign(Gtk.Align.CENTER)
        image.set_valign(Gtk.Align.CENTER)
        icon_overlay.add(image)

        tile.pack_start(icon_overlay, False, False, 0)

        name_label = Gtk.Label(label=bundle.name)
        name_label.set_line_wrap(True)
        name_label.set_max_width_chars(18)
        name_label.set_justify(Gtk.Justification.CENTER)
        name_label.set_halign(Gtk.Align.CENTER)
        tile.pack_start(name_label, False, False, 0)

        container = Gtk.EventBox()
        container.add(tile)
        container.set_visible_window(False)
        prov = _bundle_identity_provisional(bundle)
        icon_loading = self._bundle_has_remote_icon_fetch_pending(bundle)
        pulse_tile = prov or icon_loading
        if (not bundle.online) and bundle.monitored:
            container.get_style_context().add_class("offline-device-monitored")
        elif pulse_tile:
            tile.get_style_context().add_class("nn-identity-provisional")
            if prov:
                name_label.get_style_context().add_class("dim-label")
        tip = f"IP: {format_device_ip_for_details(bundle.primary)}"
        offline_mon = (not bundle.online) and bundle.monitored
        if prov and not offline_mon:
            tip += "\n" + (
                _("Provisional IPv6 identity — highlighted row pulses while discovery matches hostname / IPv4.")
            )
        if icon_loading and not offline_mon:
            tip += "\n" + _("Fetching device icon in the background — tile pulses until it arrives.")
        container.set_tooltip_text(tip)
        container.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        container.connect("button-press-event", self._on_icon_tile_button_press, bundle)
        return container

    def _on_icon_tile_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton, bundle: _DeviceBundle) -> bool:
        if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS and event.button == 1:
            self._open_device(bundle)
            return True
        if event.button == 3:
            self._show_device_context_menu(bundle, event)
            return True
        return False

    def _show_device_context_menu(self, bundle: _DeviceBundle, event: Gdk.EventButton) -> None:
        menu = Gtk.Menu()

        connect_targets = self._bundle_all_connect_targets(bundle)
        if len(connect_targets) == 1:
            label, uri = connect_targets[0]
            open_item = Gtk.MenuItem.new_with_label(_("Open ({label})").format(label=label))
            open_item.connect("activate", self._on_open_uri_activate, bundle, uri)
            menu.append(open_item)
        elif connect_targets:
            open_item = Gtk.MenuItem.new_with_label(_("Open"))
            open_submenu = Gtk.Menu()
            for label, uri in connect_targets:
                sub_item = Gtk.MenuItem.new_with_label(label)
                sub_item.connect("activate", self._on_open_uri_activate, bundle, uri)
                open_submenu.append(sub_item)
            open_item.set_submenu(open_submenu)
            menu.append(open_item)

        if (self._custom_command_template or "").strip() or self._get_bundle_custom_command(bundle):
            custom_item = Gtk.MenuItem.new_with_label(_("Run custom command"))
            custom_item.connect("activate", self._on_custom_command_activate, bundle)
            menu.append(custom_item)

        details_item = Gtk.MenuItem.new_with_label(_("Details"))
        details_item.connect("activate", self._on_show_details_activate, bundle)
        menu.append(details_item)
        icon_item = Gtk.MenuItem.new_with_label(_("Options"))
        icon_item.connect("activate", self._on_icon_item_activate, bundle)
        menu.append(icon_item)

        if bundle.monitored:
            unfollow_item = Gtk.MenuItem.new_with_label(_("Unfollow"))
            unfollow_item.connect("activate", self._on_monitor_item_activate, bundle, False)
            menu.append(unfollow_item)
        else:
            follow_item = Gtk.MenuItem.new_with_label(_("Monitor"))
            follow_item.connect("activate", self._on_monitor_item_activate, bundle, True)
            menu.append(follow_item)

        rename_item = Gtk.MenuItem.new_with_label(_("Rename"))
        rename_item.connect("activate", self._on_rename_item_activate, bundle)
        rename_item.set_sensitive(self._on_set_name_override is not None)
        menu.append(rename_item)

        location_item = Gtk.MenuItem.new_with_label(_("Location"))
        location_menu = Gtk.Menu()
        location_item.set_submenu(location_menu)
        location_item.set_sensitive(self._on_set_location_override is not None)
        current_location = None
        for device in bundle.devices:
            metadata = device.metadata if isinstance(device.metadata, dict) else {}
            value = metadata.get("user_location")
            if isinstance(value, str) and value.strip():
                current_location = value.strip()
                break
        auto_location_item = Gtk.CheckMenuItem.new_with_label(_("Auto"))
        auto_location_item.set_draw_as_radio(False)
        auto_location_item.set_active(current_location is None)
        auto_location_item.connect("activate", self._on_location_item_activate, bundle, None)
        location_menu.append(auto_location_item)
        for location_value in self._location_options:
            loc_choice_item = Gtk.CheckMenuItem.new_with_label(location_value)
            loc_choice_item.set_draw_as_radio(False)
            loc_choice_item.set_active(location_value == current_location)
            loc_choice_item.connect("activate", self._on_location_item_activate, bundle, location_value)
            location_menu.append(loc_choice_item)
        menu.append(location_item)

        type_item = Gtk.MenuItem.new_with_label(_("Device type"))
        type_menu = Gtk.Menu()
        type_item.set_submenu(type_menu)
        menu.append(type_item)
        current_type = bundle.primary.type.strip().lower() if isinstance(bundle.primary.type, str) else "unknown"
        _default_type_choices: list[tuple[str, str | None]] = [
            (_("NAS"), "nas"),
            (_("Computer"), "computer"),
            (_("Router"), "router"),
            (_("Media server"), "mediaserver"),
            (_("Printer"), "printer"),
            (_("Multifunction printer"), "multifunction_printer"),
            (_("Printer (network / IPP)"), "networkprinter"),
            (_("SmartSpeaker"), "smartspeaker"),
            (_("SmartTV"), "smarttv"),
            (_("SmartDevice"), "smartdevice"),
            (_("Camera"), "camera"),
            (_("HomeAppliance"), "homeappliance"),
            (_("CNC"), "cnc"),
            (_("3D printer"), "3dprinter"),
        ]
        type_choices: list[tuple[str, str | None]] = [(_("Auto"), None)] + (
            [(lbl, slug) for lbl, slug in self._type_options]
            if self._type_options
            else _default_type_choices
        )
        known_types = {value for _label, value in type_choices if value is not None}
        for label, type_value in type_choices:
            item = Gtk.CheckMenuItem.new_with_label(label)
            item.set_draw_as_radio(False)
            if type_value is None:
                item.set_active(current_type not in known_types)
            else:
                item.set_active(current_type == type_value)
            item.connect("activate", self._on_type_item_activate, bundle, type_value)
            type_menu.append(item)

        menu.show_all()
        menu.popup_at_pointer(event)

    def _on_monitor_item_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle, monitored: bool) -> None:
        if self._on_set_monitored is None:
            return
        for device in bundle.devices:
            self._on_set_monitored(device, monitored)

    def _apply_bundle_icon_settings(self, bundle: _DeviceBundle, mode: str, custom_icon_name: str | None) -> None:
        endpoint = (bundle.ip, bundle.port)
        current_mode = self._normalized_icon_mode(bundle)
        if mode == current_mode:
            changed = False
            if mode != "custom":
                if endpoint in self._custom_icon_overrides:
                    self._custom_icon_overrides.pop(endpoint, None)
                    changed = True
            elif custom_icon_name:
                previous = self._custom_icon_overrides.get(endpoint)
                if previous != custom_icon_name:
                    self._custom_icon_overrides[endpoint] = custom_icon_name
                    changed = True
            if changed:
                self._rebuild_icon_sections(self._filtered_devices)
                if self._on_icon_mode_changed is not None:
                    self._on_icon_mode_changed()
            return
        self._icon_source_overrides[endpoint] = mode
        if mode == "custom":
            if custom_icon_name:
                self._custom_icon_overrides[endpoint] = custom_icon_name
        else:
            self._custom_icon_overrides.pop(endpoint, None)
        self._rebuild_icon_sections(self._filtered_devices)
        if self._on_icon_mode_changed is not None:
            self._on_icon_mode_changed()

    def _on_rename_item_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle) -> None:
        if self._on_set_name_override is None:
            return
        parent = self._parent_window
        if parent is None:
            top_level = self.get_toplevel()
            if isinstance(top_level, Gtk.Window):
                parent = top_level
        if parent is None:
            return
        dialog = Gtk.Dialog(title=_("Rename device"), transient_for=parent, modal=True)
        prepare_gtk_dialog(dialog)
        dialog.set_default_size(360, -1)
        content = dialog.get_content_area()
        content.set_border_width(8)
        label = Gtk.Label(label=_("Custom name"), xalign=0.0)
        entry = Gtk.Entry()
        entry.set_text(bundle.name)
        entry.select_region(0, -1)
        content.pack_start(label, False, False, 4)
        content.pack_start(entry, False, False, 4)
        dialog.add_button(_("Reset"), Gtk.ResponseType.REJECT)
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Save"), Gtk.ResponseType.OK)
        dialog.show_all()

        response = dialog.run()
        new_name: str | None = None
        if response == Gtk.ResponseType.OK:
            value = entry.get_text().strip()
            new_name = value if value else None
        elif response == Gtk.ResponseType.REJECT:
            new_name = None
        dialog.destroy()
        if response not in {Gtk.ResponseType.OK, Gtk.ResponseType.REJECT}:
            return
        for device in bundle.devices:
            self._on_set_name_override(device.source, device.ip, device.port, new_name)

    def _on_location_item_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle, value: str | None) -> None:
        if self._on_set_location_override is None:
            return
        for device in bundle.devices:
            self._on_set_location_override(device.source, device.ip, device.port, value)

    def _on_type_item_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle, device_type: str | None) -> None:
        if self._on_set_type_override is None:
            return
        self._on_set_type_override(bundle.primary.source, bundle.primary.ip, bundle.primary.port, device_type)

    def _on_open_item_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle) -> None:
        self._open_device(bundle)

    def _bundle_all_connect_targets(self, bundle: _DeviceBundle) -> list[tuple[str, str]]:
        return resolve_all_connect_targets(
            bundle_ip=bundle.ip,
            primary_type=bundle.primary.type or "",
            devices=bundle.devices,
        )

    def _on_open_uri_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle, uri: str) -> None:
        d = bundle.primary

        def _spawn_err(msg: str) -> None:
            GLib.idle_add(
                lambda: self._show_command_error(
                    _("Could not start the command: {error}").format(error=msg),
                )
            )

        err = launch_connect_for_uri(
            uri,
            self._connect_command_templates,
            ip=bundle.ip,
            port=int(bundle.port),
            name=bundle.name or "",
            type_=d.type or "",
            category=d.category or "",
            device_cmd_override=self._get_bundle_custom_command(bundle),
            on_spawn_error=_spawn_err,
        )
        if err:
            self._show_command_error(err)

    def _get_bundle_custom_command(self, bundle: _DeviceBundle) -> str:
        """Return the per-device custom command from metadata, or empty string."""
        for dev in bundle.devices:
            md = dev.metadata if isinstance(dev.metadata, dict) else {}
            cmd = md.get("custom_command")
            if isinstance(cmd, str) and cmd.strip():
                return cmd.strip()
        return ""

    def _on_custom_command_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle) -> None:
        per_device_cmd = self._get_bundle_custom_command(bundle)
        tmpl = per_device_cmd or (self._custom_command_template or "").strip()
        if not tmpl:
            return
        d = bundle.primary
        argv = build_argv_from_template(
            tmpl,
            ip=bundle.ip,
            port=int(bundle.port),
            name=bundle.name or "",
            type_=d.type or "",
            category=d.category or "",
            url="",
        )
        if argv is None:
            self._show_command_error(_("Could not parse the custom command (check placeholders)."))
            return

        def _on_err(msg: str) -> None:
            self._show_command_error(
                _("Could not start the command: {error}").format(error=msg),
            )

        spawn_custom_command_detached(argv, on_error=_on_err, schedule_on_main=gtk_idle_schedule)

    def _show_command_error(self, message: str) -> None:
        parent = self._parent_window
        if parent is None:
            top = self.get_toplevel()
            if isinstance(top, Gtk.Window):
                parent = top
        dlg = Gtk.MessageDialog(
            transient_for=parent,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=message,
        )
        prepare_gtk_dialog(dlg)
        dlg.run()
        dlg.destroy()

    def _on_show_details_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle) -> None:
        self._open_any_details(bundle)

    def _open_any_details(self, bundle: _DeviceBundle, initial_tab: str | None = None) -> None:
        ipv6 = collect_link_local_ipv6(
            bundle.wsdd_device,
            bundle.wsd_device,
            bundle.mdns_device,
            self._effective_ssdp_device(bundle),
            bundle.nmb_device,
        )
        overview = {
            "name": bundle.name or "",
            "ip": format_device_ip_for_details(bundle.primary),
            "location": self._bundle_location_label(bundle),
            "type": format_device_type_for_details(bundle.primary),
            "ipv6_link_local": ipv6 or "",
        }
        ssdp = self._effective_ssdp_device(bundle)
        mdns = bundle.mdns_device
        raw_xml: str | None = None
        xml_location: str | None = None
        services_list: list[tuple[str, str, str]] | None = None
        troubleshooting_fields: list[tuple[str, str]] | None = None
        mdns_sections: list[dict] | None = None
        if ssdp is not None and mdns is not None:
            f_ssdp, raw_xml, services_list, troubleshooting_fields, xml_location = build_ssdp_payload(ssdp)
            f_mdns, mdns_sections = build_mdns_payload(mdns)
            fields = with_aggregate_ports_field(merge_ssdp_mdns_detail_fields(f_ssdp, f_mdns), ssdp, mdns)
            details_field_rule_map = {
                "Friendly name": "xml:friendlyName",
                "Information": "meta:information",
                "Hostname (mDNS)": "meta:hostname",
                "Server (mDNS)": "meta:server",
            }
        elif ssdp is not None:
            fields, raw_xml, services_list, troubleshooting_fields, xml_location = build_ssdp_payload(ssdp)
            fields = with_aggregate_ports_field(fields, ssdp)
            details_field_rule_map = {
                "Friendly name": "xml:friendlyName",
                "Information": "meta:information",
                "Model": "xml:modelName",
                "Manufacturer": "xml:manufacturer",
            }
        elif mdns is not None:
            fields, mdns_sections = build_mdns_payload(mdns)
            fields = with_aggregate_ports_field(fields, mdns)
            details_field_rule_map = {
                "Hostname": "meta:hostname",
                "Server": "meta:server",
                "Information": "meta:information",
            }
        else:
            fields = build_wsd_family_detail_fields(
                bundle.wsdd_device,
                bundle.wsd_device,
                bundle.nmb_device,
            )
            details_field_rule_map = {}
        fields = dedupe_last_seen_in_fields(strip_detail_fields_covered_by_overview(fields, overview))
        current_url_override: str | None = None
        for _dev in bundle.devices:
            _md = _dev.metadata if isinstance(_dev.metadata, dict) else {}
            _v = _md.get("url_override")
            if isinstance(_v, str) and _v.strip():
                current_url_override = _v.strip()
                break
        # Collect per-device commands from bundle devices
        current_device_cmds: list[dict] = []
        for _dev in bundle.devices:
            _md = _dev.metadata if isinstance(_dev.metadata, dict) else {}
            _cmds = _md.get("device_commands")
            if isinstance(_cmds, list) and _cmds:
                current_device_cmds = _cmds
                break
        # Collect per-device custom command
        current_custom_cmd: str | None = None
        for _dev in bundle.devices:
            _md = _dev.metadata if isinstance(_dev.metadata, dict) else {}
            _cc = _md.get("custom_command")
            if isinstance(_cc, str) and _cc.strip():
                current_custom_cmd = _cc.strip()
                break
        self._show_details_dialog(
            title=f"{_('Device details')} - {bundle.name}",
            fields=fields,
            raw_content=raw_xml,
            raw_button_label=_("Show raw XML") if raw_xml else None,
            services_list=services_list,
            mdns_service_sections=mdns_sections,
            troubleshooting_records=troubleshooting_fields,
            raw_xml_location=xml_location,
            icon_mode=self._normalized_icon_mode(bundle),
            selected_icon_id=self._custom_icon_overrides.get((bundle.ip, bundle.port)),
            icon_choices=self._load_icon_choices(bundle.primary.type),
            on_apply_icon_settings=lambda mode, custom: self._apply_bundle_icon_settings(bundle, mode, custom),
            custom_icons_dir=str(self._custom_icons_dir),
            initial_tab=initial_tab,
            has_device_icon_source=self._bundle_has_remote_device_icon(bundle),
            provided_icon_display=self._bundle_provided_icon_display(bundle),
            details_field_rule_map=details_field_rule_map,
            endpoint_source=bundle.primary.source,
            endpoint_ip=bundle.primary.ip,
            endpoint_port=int(bundle.primary.port),
            overview=overview,
            url_override=current_url_override,
            on_set_url_override=(
                None if self._on_set_url_override is None else
                lambda url, _b=bundle: [
                    self._on_set_url_override(d.source, d.ip, d.port, url)
                    for d in _b.devices
                ]
            ),
            device_commands=current_device_cmds or None,
            on_set_device_commands=(
                None if self._on_set_device_commands is None else
                lambda cmds, _b=bundle: [
                    self._on_set_device_commands(d.source, d.ip, d.port, cmds)
                    for d in _b.devices
                ]
            ),
            custom_command=current_custom_cmd,
            on_set_custom_command=(
                None if self._on_set_custom_command is None else
                lambda cmd, _b=bundle: [
                    self._on_set_custom_command(d.source, d.ip, d.port, cmd)
                    for d in _b.devices
                ]
            ),
        )

    def _on_icon_item_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle) -> None:
        self._open_any_details(bundle, initial_tab="options")

    def _bundle_connect_target(self, bundle: _DeviceBundle) -> str | None:
        return resolve_connect_target(
            bundle_ip=bundle.ip,
            primary_type=bundle.primary.type or "",
            devices=bundle.devices,
        )

    def _open_device(self, bundle: _DeviceBundle) -> None:
        """Double-click / Open menu: HTTP(S) → SMB → FTP → SSH → Telnet; *computer* → SMB if nothing else; else no-op."""
        target = self._bundle_connect_target(bundle)
        if not target:
            return
        d = bundle.primary

        def _spawn_err(msg: str) -> None:
            GLib.idle_add(
                lambda: self._show_command_error(
                    _("Could not start the command: {error}").format(error=msg),
                )
            )

        err = launch_connect_for_uri(
            target,
            self._connect_command_templates,
            ip=bundle.ip,
            port=int(bundle.port),
            name=bundle.name or "",
            type_=d.type or "",
            category=d.category or "",
            on_spawn_error=_spawn_err,
        )
        if err:
            self._show_command_error(err)

    def _show_details_dialog(
        self,
        title: str,
        fields: list[tuple[str, str]],
        services_list: list[tuple[str, str, str]] | None = None,
        txt_records: list[tuple[str, str]] | None = None,
        mdns_service_sections: list[dict] | None = None,
        raw_content: str | None = None,
        raw_button_label: str | None = None,
        troubleshooting_records: list[tuple[str, str]] | None = None,
        raw_xml_location: str | None = None,
        icon_mode: str | None = None,
        selected_icon_id: str | None = None,
        icon_choices: list[tuple[str, str, GdkPixbuf.Pixbuf]] | None = None,
        on_apply_icon_settings=None,
        custom_icons_dir: str | None = None,
        initial_tab: str | None = None,
        has_device_icon_source: bool = True,
        provided_icon_display: str | None = None,
        details_field_rule_map: dict[str, str] | None = None,
        endpoint_source: str | None = None,
        endpoint_ip: str | None = None,
        endpoint_port: int | None = None,
        overview: dict[str, str | None] | None = None,
        url_override: str | None = None,
        on_set_url_override=None,
        device_commands: list[dict] | None = None,
        on_set_device_commands=None,
        custom_command: str | None = None,
        on_set_custom_command=None,
    ) -> None:
        parent = self._parent_window
        if parent is None:
            top_level = self.get_toplevel()
            if isinstance(top_level, Gtk.Window):
                parent = top_level
        if parent is None:
            return
        active_rules = {}
        if (
            self._on_get_field_rules is not None
            and isinstance(endpoint_source, str)
            and isinstance(endpoint_ip, str)
            and isinstance(endpoint_port, int)
        ):
            active_rules = self._on_get_field_rules(endpoint_source, endpoint_ip, endpoint_port) or {}
        dialog = DeviceDetailsDialog(
            parent=parent,
            title=title,
            fields=fields,
            services_records=services_list,
            txt_records=txt_records,
            mdns_service_sections=mdns_service_sections,
            raw_content=raw_content,
            raw_button_label=raw_button_label,
            troubleshooting_records=troubleshooting_records,
            raw_xml_location=raw_xml_location,
            icon_mode=icon_mode,
            selected_icon_id=selected_icon_id,
            icon_choices=icon_choices,
            on_apply_icon_settings=on_apply_icon_settings,
            custom_icons_dir=custom_icons_dir,
            initial_tab=initial_tab,
            has_device_icon_source=has_device_icon_source,
            provided_icon_display=provided_icon_display,
            details_field_rule_map=details_field_rule_map or {},
            overview=overview,
            url_override=url_override,
            on_set_url_override=on_set_url_override,
            device_commands=device_commands,
            on_set_device_commands=on_set_device_commands,
            custom_command=custom_command,
            on_set_custom_command=on_set_custom_command,
            active_field_rules=active_rules,
            on_add_field_rule=(
                (lambda target, path: self._on_set_field_rule(endpoint_source, endpoint_ip, endpoint_port, target, path))
                if (
                    self._on_set_field_rule is not None
                    and isinstance(endpoint_source, str)
                    and isinstance(endpoint_ip, str)
                    and isinstance(endpoint_port, int)
                )
                else None
            ),
            on_remove_field_rule=(
                (
                    lambda target, path: self._on_remove_field_rule(
                        endpoint_source,
                        endpoint_ip,
                        endpoint_port,
                        target,
                        path,
                    )
                )
                if (
                    self._on_remove_field_rule is not None
                    and isinstance(endpoint_source, str)
                    and isinstance(endpoint_ip, str)
                    and isinstance(endpoint_port, int)
                )
                else None
            ),
        )
        dialog.run()
        dialog.destroy()

    def _has_ssdp_details(self, bundle: _DeviceBundle) -> bool:
        return self._effective_ssdp_device(bundle) is not None

    def _has_mdns_details(self, bundle: _DeviceBundle) -> bool:
        return bundle.mdns_device is not None

    def _effective_ssdp_device(self, bundle: _DeviceBundle) -> Device | None:
        if bundle.ssdp_device is not None:
            return bundle.ssdp_device
        mdns = bundle.mdns_device
        if mdns is None or not isinstance(mdns.metadata, dict):
            return None
        mdns_meta = mdns.metadata
        xml_fields = mdns_meta.get("xml_fields")
        if not isinstance(xml_fields, dict) or not xml_fields:
            return None
        if not any(
            isinstance(xml_fields.get(k), str) and xml_fields.get(k).strip()
            for k in ("friendlyName", "deviceType", "UDN", "modelName", "manufacturer", "services_description")
        ):
            return None
        synth_meta = dict(mdns_meta)
        synth_meta["xml_fields"] = dict(xml_fields)
        return Device(
            name=mdns.name,
            ip=mdns.ip,
            port=mdns.port,
            type=mdns.type,
            category=mdns.category,
            source="ssdp",
            url=mdns.url,
            metadata=synth_meta,
            last_seen=mdns.last_seen,
            online=mdns.online,
            monitored=mdns.monitored,
            icon=mdns.icon,
        )

    def _bundle_provided_icon_display(self, bundle: _DeviceBundle) -> str | None:
        """Display source for a device-provided icon (URL/cached canonical URL), if available."""
        for dev in (bundle.ssdp_device, bundle.wsdd_device, bundle.wsd_device, bundle.nmb_device, bundle.mdns_device):
            if dev is None:
                continue
            url = self._resolved_remote_icon_url(dev)
            if url:
                return url
            cached = self._cached_remote_icon_url_for_host(dev.ip)
            if cached:
                return cached
        return None

    def _bundle_has_remote_device_icon(self, bundle: _DeviceBundle) -> bool:
        for dev in (bundle.ssdp_device, bundle.wsdd_device, bundle.wsd_device, bundle.nmb_device, bundle.mdns_device):
            if dev is None:
                continue
            if self._remote_icon_url_for_device(dev):
                return True
            if self._cached_remote_icon_url_for_host(dev.ip):
                return True
        return False

    def _cached_remote_icon_url_for_host(self, ip: str) -> str | None:
        sip = str(ip).strip()
        if not sip or sip in {"0.0.0.0", "::"}:
            return None
        self._ensure_remote_icon_index()
        row = self._remote_icon_index.get(sip)
        if not isinstance(row, dict):
            return None
        canon = row.get("canonical_url")
        if not isinstance(canon, str) or not canon.strip():
            return None
        payload_sha = row.get("payload_sha256")
        if not isinstance(payload_sha, str) or not payload_sha.strip():
            return None
        payload_path = self._remote_icon_payload_path(payload_sha.strip())
        if not payload_path.exists():
            return None
        return canon.strip()

    def _remote_icon_url_for_device(self, device: Device) -> str | None:
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        if device.source == "ssdp":
            xml_fields = metadata.get("xml_fields")
            if not isinstance(xml_fields, dict):
                return None
            icon_url = xml_fields.get("iconURL")
            if not isinstance(icon_url, str) or not icon_url.strip():
                return None
            return self._normalize_icon_url(icon_url.strip(), device.ip, device.port)
        if device.source == "mdns":
            for url in self._mdns_icon_urls_from_metadata(metadata, device.ip, device.port):
                if url:
                    return url
        return None

    def _iter_remote_icon_disk_keys(self, device: Device) -> list[str]:
        """Distinct canonical URLs (+ aliases + last-known binding per IP) to match RAM/disk cache."""
        keys: list[str] = []
        seen: set[str] = set()
        sip = str(device.ip).strip()
        raw_list: list[str] = []

        def push_logical_url(candidate: str) -> None:
            if not isinstance(candidate, str) or not candidate.strip():
                return
            base = DeviceList._canonical_remote_icon_url(candidate.strip())
            for alias in DeviceList._expand_cached_remote_icon_aliases(base):
                if alias not in seen:
                    seen.add(alias)
                    keys.append(alias)

        self._ensure_remote_icon_index()
        if sip and device.source in {"mdns", "ssdp"}:
            entry = self._remote_icon_index.get(sip)
            if isinstance(entry, dict):
                remembered = entry.get("canonical_url")
                if isinstance(remembered, str) and remembered.strip():
                    push_logical_url(remembered)

        if device.source == "ssdp":
            single = self._remote_icon_url_for_device(device)
            if single:
                raw_list.append(single)
        elif device.source == "mdns":
            metadata = device.metadata if isinstance(device.metadata, dict) else {}
            raw_list.extend(u for u in self._mdns_icon_urls_from_metadata(metadata, sip, device.port) if u)

        for raw in raw_list:
            if not isinstance(raw, str) or not raw.strip():
                continue
            ref = raw.strip()
            rewrote = self._rewrite_remote_icon_url_to_device_ip(ref, sip)
            push_logical_url(rewrote)
            push_logical_url(ref)

        return keys

    def _primary_remote_icon_fetch_pair(self, device: Device) -> tuple[str, str] | None:
        """Returns (fetch_url, cache_key for disk) using the preferred advertised icon."""
        raw = self._remote_icon_url_for_device(device)
        if not isinstance(raw, str) or not raw.strip():
            return None
        sip = str(device.ip).strip()
        fetch_url = self._rewrite_remote_icon_url_to_device_ip(raw.strip(), sip).strip()
        if not fetch_url:
            return None
        cache_key = self._canonical_remote_icon_url(fetch_url)
        if not cache_key:
            return None
        return fetch_url, cache_key

    @staticmethod
    def _normalize_icon_url(ref: str, ip: str, port: int) -> str:
        ref = ref.strip()
        if ref.startswith(("http://", "https://")):
            return ref
        try:
            p = int(port) if port else 80
        except (TypeError, ValueError):
            p = 80
        base = f"http://{ip}:{p}"
        if ref.startswith("/"):
            return base + ref
        return f"{base}/{ref.lstrip('/')}"

    @staticmethod
    def _canonical_remote_icon_url(icon_url: str) -> str:
        """Stable cache / dedup key: scheme + normalized host/port, omit default ports (fixes sha256 mismatches vs older cache)."""
        u = icon_url.strip()
        try:
            p = urlparse(u)
        except ValueError:
            return u
        if not p.scheme or not p.hostname:
            return u
        scheme = (p.scheme or "").lower()
        host_txt = (p.hostname or "").lower().rstrip(".")
        port = p.port

        host_literal = host_txt
        try:
            parsed_ip = ipaddress.ip_address(host_txt)
            if isinstance(parsed_ip, ipaddress.IPv6Address):
                host_literal = f"[{parsed_ip.compressed}]"
        except ValueError:
            host_literal = host_txt

        omit_default = (scheme == "http" and port in {None, 80}) or (scheme == "https" and port in {None, 443})

        netloc_final: str
        if omit_default:
            netloc_final = host_literal
        elif port is not None:
            netloc_final = f"{host_literal}:{port}"
        else:
            netloc_final = host_literal

        path = p.path.strip() if p.path else ""
        if not path:
            path = "/"
        query = (p.query or "").strip()

        # Ignore fragment fragment is not preserved in urlparse for url unparse similarly
        return urlunparse((scheme, netloc_final, path, "", query, ""))

    @staticmethod
    def _expand_cached_remote_icon_aliases(canonical_key: str) -> list[str]:
        """Alternate canonical URL forms for the same resource (e.g. default vs explicit port)."""
        out: list[str] = []
        seen: set[str] = set()
        ck = (canonical_key or "").strip()

        def add(s: str) -> None:
            if s and s not in seen:
                seen.add(s)
                out.append(s)

        if not ck:
            return out

        try:
            p = urlparse(ck)
        except ValueError:
            add(ck)
            return out
        add(ck)
        if not p.scheme or not p.hostname:
            return out

        scheme = (p.scheme or "").lower()
        host_txt = (p.hostname or "").lower().rstrip(".")
        port = p.port
        path = (p.path or "").strip() or "/"
        query = p.query or ""

        try:
            parsed_ip = ipaddress.ip_address(host_txt)
            if isinstance(parsed_ip, ipaddress.IPv6Address):
                host_literal = f"[{parsed_ip.compressed}]"
            else:
                host_literal = host_txt
        except ValueError:
            host_literal = host_txt

        def build(netloc_piece: str) -> str:
            return urlunparse((scheme, netloc_piece, path, "", query, ""))

        if scheme == "http":
            # implicit default port ↔ explicit :80
            if port in {None, 80}:
                add(build(f"{host_literal}:80"))
        elif scheme == "https" and port in {None, 443}:
            add(build(f"{host_literal}:443"))

        return out

    @staticmethod
    def _normalize_host_icon_index_row(row: dict) -> dict[str, str] | None:
        raw_url = row.get("canonical_url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            return None
        canonical = DeviceList._canonical_remote_icon_url(raw_url.strip())
        sha = row.get("payload_sha256")
        sl = sha.lower() if isinstance(sha, str) else ""
        if len(sl) != 64 or not all(c in "0123456789abcdef" for c in sl):
            sl = DeviceList._remote_icon_digest(canonical)
        ts = row.get("updated_at")
        if not isinstance(ts, str) or not ts.strip():
            ts = datetime.now(timezone.utc).isoformat()
        return {"canonical_url": canonical, "payload_sha256": sl, "updated_at": ts}

    def _ensure_remote_icon_index(self) -> None:
        if self._remote_icon_index_loaded:
            return
        self._remote_icon_index_loaded = True
        index_path = self._remote_icon_index_path
        try:
            if not index_path.is_file():
                return
            data = json.loads(index_path.read_text(encoding="utf-8"))
            ver_raw = data.get("version", 0) if isinstance(data, dict) else 0
            try:
                vern = int(ver_raw)
            except (TypeError, ValueError):
                vern = 0
            if isinstance(data, dict) and vern >= 2:
                hosts = data.get("hosts")
                if isinstance(hosts, dict):
                    for sip, row in hosts.items():
                        if not isinstance(sip, str) or not isinstance(row, dict):
                            continue
                        entry = self._normalize_host_icon_index_row(row)
                        if entry is not None:
                            self._remote_icon_index[sip.strip()] = entry
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return

    def _write_remote_icon_index_file(self) -> None:
        path = self._remote_icon_index_path
        tmp = path.with_suffix(".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            blob = {"version": 2, "hosts": dict(sorted(self._remote_icon_index.items()))}
            tmp.write_text(json.dumps(blob, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            try:
                if tmp.is_file():
                    tmp.unlink()
            except OSError:
                pass

    def _persist_remote_icon_index_entry(self, sip: str, canonical_url: str) -> None:
        if not sip or not isinstance(canonical_url, str) or not canonical_url.strip():
            return
        self._ensure_remote_icon_index()
        row = self._normalize_host_icon_index_row(
            {"canonical_url": canonical_url.strip(), "updated_at": datetime.now(timezone.utc).isoformat()}
        )
        if row is None:
            return
        prev = self._remote_icon_index.get(sip.strip())
        if isinstance(prev, dict):
            if prev.get("canonical_url") == row["canonical_url"] and prev.get("payload_sha256") == row["payload_sha256"]:
                return
        self._remote_icon_index[sip.strip()] = row
        self._write_remote_icon_index_file()

    @staticmethod
    def _should_rewrite_icon_hostname_to_lan_ip(hostname: str) -> bool:
        """Replace mDNS-style names with the device IP for fetch/cache stability."""
        h = (hostname or "").lower().rstrip(".")
        if not h:
            return False
        try:
            ipaddress.ip_address(h)
            return False
        except ValueError:
            pass
        if h.endswith(".local") or h.endswith(".lan"):
            return True
        if "." not in h:
            return True
        return False

    @staticmethod
    def _rewrite_remote_icon_url_to_device_ip(url: str, ip: str) -> str:
        """Use device IP as host for typical LAN icon URLs (avoids .local resolution / hash drift)."""
        ip_stripped = str(ip).strip()
        if not ip_stripped or ip_stripped in {"0.0.0.0", "::"}:
            return url.strip()
        try:
            p = urlparse(url.strip())
        except ValueError:
            return url.strip()
        if p.scheme.lower() not in {"http", "https"} or not p.netloc:
            return url.strip()
        host = p.hostname
        if not host or not DeviceList._should_rewrite_icon_hostname_to_lan_ip(host):
            return url.strip()
        try:
            dev_ip = ipaddress.ip_address(ip_stripped)
        except ValueError:
            return url.strip()
        port = p.port
        if isinstance(dev_ip, ipaddress.IPv6Address):
            netloc = f"[{ip_stripped}]"
            if port is not None:
                netloc += f":{port}"
        else:
            netloc = ip_stripped
            if port is not None:
                netloc += f":{port}"
        path = p.path or "/"
        return urlunparse((p.scheme.lower(), netloc, path, "", p.query, ""))

    def _resolved_remote_icon_url(self, device: Device) -> str | None:
        """Canonical URL used for fetch, RAM cache, and disk (IP host when applicable)."""
        raw = self._remote_icon_url_for_device(device)
        if not raw:
            return None
        mapped = self._rewrite_remote_icon_url_to_device_ip(raw, device.ip)
        return self._canonical_remote_icon_url(mapped)

    @staticmethod
    def _urlopen_remote_icon(url: str):
        """HTTPS for LAN printers (.local / private IP) often uses self-signed certs — relax verify only there."""
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower().rstrip(".")
        ctx = None
        if scheme == "https":
            allow_insecure = host.endswith(".local") or host.endswith(".lan")
            if not allow_insecure and host:
                try:
                    allow_insecure = ipaddress.ip_address(host).is_private
                except ValueError:
                    pass
            if allow_insecure:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
        timeout = 4.5 if scheme == "https" else 2.0
        if ctx is not None:
            return urlopen(url, timeout=timeout, context=ctx)
        return urlopen(url, timeout=timeout)

    @staticmethod
    def _mdns_preferred_icon_base_port(metadata: dict, fallback_port: int) -> int:
        """Sonos-like hosts: TXT icon paths are often served by the `_http._tcp` port, not ephemeral mDNS ports."""
        if not isinstance(metadata, dict):
            return fallback_port
        services = metadata.get("services")
        if not isinstance(services, list):
            return fallback_port
        for svc in services:
            if not isinstance(svc, dict):
                continue
            name = str(svc.get("service", "")).lower()
            if "_http._tcp" not in name:
                continue
            try:
                candidate = int(svc.get("port", 0) or 0)
            except (TypeError, ValueError):
                continue
            if candidate > 0:
                return candidate
        try:
            return int(fallback_port) if fallback_port else 80
        except (TypeError, ValueError):
            return 80

    def _mdns_icon_urls_from_metadata(self, metadata: dict, ip: str, port: int) -> list[str]:
        icon_keys = frozenset({"representation", "icon", "iconurl"})
        urls: list[str] = []
        seen: set[str] = set()
        base_port = self._mdns_preferred_icon_base_port(metadata, port)

        def _push(raw_val: str) -> None:
            u = self._normalize_icon_url(raw_val.strip(), ip, base_port)
            if u not in seen:
                seen.add(u)
                urls.append(u)

        services = metadata.get("services")
        if isinstance(services, list):
            for svc in services:
                if not isinstance(svc, dict):
                    continue
                txt = svc.get("txt")
                if not isinstance(txt, dict):
                    continue
                for key, val in txt.items():
                    if str(key).strip().lower() not in icon_keys:
                        continue
                    if not isinstance(val, str) or not val.strip():
                        continue
                    _push(val)
        txt_top = metadata.get("txt")
        if isinstance(txt_top, dict):
            for key, val in txt_top.items():
                if str(key).strip().lower() not in icon_keys:
                    continue
                if not isinstance(val, str) or not val.strip():
                    continue
                _push(val)
        return urls

    def _apply_category_filter(self, bundles: list[_DeviceBundle]) -> list[_DeviceBundle]:
        if not self._category_filter:
            return list(bundles)
        if isinstance(self._category_filter, str) and self._category_filter.startswith("location:"):
            location_value = self._category_filter.split(":", 1)[1]
            if location_value == "__none__":
                return [bundle for bundle in bundles if self._bundle_location_label(bundle) == _("No location")]
            return [bundle for bundle in bundles if self._bundle_location_label(bundle) == location_value]
        cf = self._category_filter
        if not isinstance(cf, str):
            return list(bundles)
        out: list[_DeviceBundle] = []
        for bundle in bundles:
            slug = (bundle.primary.type or "unknown").strip().lower()
            if slug == cf.strip().lower():
                out.append(bundle)
            elif bundle.category == cf:
                out.append(bundle)
        return out

    def _on_tree_row_activated(self, _tree: Gtk.TreeView, path: Gtk.TreePath, _column: Gtk.TreeViewColumn) -> None:
        model = self._tree.get_model()
        tree_iter = model.get_iter(path)
        bundle = model.get_value(tree_iter, 0)
        if isinstance(bundle, _DeviceBundle):
            self._open_device(bundle)

    def _on_tree_button_press(self, tree: Gtk.TreeView, event: Gdk.EventButton) -> bool:
        if event.button == 3:
            hit = tree.get_path_at_pos(int(event.x), int(event.y))
            if hit is None:
                return False
            path, _column, _x, _y = hit
            tree.set_cursor(path, None, False)
            model = tree.get_model()
            tree_iter = model.get_iter(path)
            bundle = model.get_value(tree_iter, 0)
            if isinstance(bundle, _DeviceBundle):
                self._show_device_context_menu(bundle, event)
                return True
        return False

    def _load_device_icon(self, bundle: _DeviceBundle) -> GdkPixbuf.Pixbuf:
        device = bundle.primary
        # Overrides and list row identity use bundle.{ip,port}; primary may differ after merge.
        endpoint = (bundle.ip, bundle.port)
        icon_mode = self._icon_source_overrides.get(endpoint, "provided")
        use_custom = icon_mode == "custom"
        use_provided = icon_mode in {"auto", "provided"}
        if use_custom:
            custom_pixbuf = self._load_custom_icon_for_endpoint(endpoint, 64)
            if custom_pixbuf is not None:
                return custom_pixbuf
            # If custom icon is missing, fallback to provided behavior.
            use_provided = True
        if use_provided:
            for cand in (bundle.ssdp_device, bundle.wsdd_device, bundle.wsd_device, bundle.nmb_device, bundle.mdns_device):
                if cand is None:
                    continue
                remote_icon = self._load_remote_icon(cand, 64)
                if remote_icon is not None:
                    return remote_icon
            for cand in (bundle.ssdp_device, bundle.wsdd_device, bundle.wsd_device, bundle.nmb_device, bundle.mdns_device):
                if cand is None:
                    continue
                if self._remote_icon_fetch_pending(cand):
                    icon_path = resolve_icon_path(cand.icon or device.icon)
                    try:
                        return GdkPixbuf.Pixbuf.new_from_file_at_size(str(icon_path), 64, 64)
                    except Exception:
                        break
            icon_path = resolve_icon_path(device.icon)
            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_size(str(icon_path), 64, 64)
            except Exception as exc:
                _logger.debug(
                    "Icon: bundled PNG failed ip=%s type=%s icon_field=%r path=%s err=%s",
                    device.ip,
                    device.type,
                    device.icon,
                    icon_path,
                    exc,
                )

        _logger.debug(
            "Icon: bundled-freedesktop type fallback ip=%s port=%s type=%s icon_mode=%s name=%r",
            device.ip,
            device.port,
            device.type,
            icon_mode,
            device.name,
        )
        slug = (device.type or "unknown").strip().lower()
        for icon_name in type_icon_basenames_for_slug(slug):
            p = resolve_bundled_freedesktop_icon(icon_name)
            if p is None:
                continue
            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_size(str(p), 64, 64)
            except Exception:
                continue
        return GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 64, 64)

    def _normalized_icon_mode(self, bundle: _DeviceBundle) -> str:
        raw_mode = self._icon_source_overrides.get((bundle.ip, bundle.port), "provided")
        if raw_mode == "auto":
            return "provided"
        if raw_mode in {"provided", "system", "custom"}:
            return raw_mode
        return "provided"

    def _load_custom_icon_for_endpoint(self, endpoint: tuple[str, int], size: int) -> GdkPixbuf.Pixbuf | None:
        icon_id = self._custom_icon_overrides.get(endpoint)
        if not icon_id:
            return None
        icon_path = self._resolve_icon_id_to_path(icon_id)
        if not icon_path.exists():
            return None
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_size(str(icon_path), size, size)
        except Exception:
            return None

    def _load_icon_choices(self, preferred_type: str | None = None) -> list[tuple[str, str, GdkPixbuf.Pixbuf]]:
        choices: list[tuple[str, str, GdkPixbuf.Pixbuf]] = []
        try:
            for icon_id, label, path in iter_icon_picker_entries(preferred_type):
                if path is None or not path.is_file():
                    continue
                try:
                    preview = GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), 64, 64)
                except Exception:
                    continue
                choices.append((icon_id, label, preview))
        except Exception:
            return choices
        return choices

    def _resolve_icon_id_to_path(self, icon_id: str) -> Path:
        if not isinstance(icon_id, str) or not icon_id:
            return self._custom_icons_dir / ""
        if ":" in icon_id:
            source, name = icon_id.split(":", 1)
            name = name.strip()
            if source == "bundled":
                p = resolve_bundled_freedesktop_icon(Path(name).stem)
                if p is not None:
                    return p
                return self._builtin_icons_dir / "__nn_missing_icon__.png"
            if source == "builtin":
                return self._builtin_icons_dir / name
            if source == "custom":
                return self._custom_icons_dir / name
        return self._builtin_icons_dir / icon_id

    def _load_remote_icon(self, device: Device, size: int) -> GdkPixbuf.Pixbuf | None:
        endpoint = (device.ip, device.port)
        sip = str(device.ip).strip()

        host_cached = self._remote_icon_by_host_ip.get(sip) if sip else None
        if host_cached is not None:
            scaled = self._normalize_icon_pixbuf(host_cached, size)
            self._remote_icon_by_endpoint[endpoint] = scaled
            return scaled

        def _register_hit(pixbuf: GdkPixbuf.Pixbuf, binding_disk_key: str | None = None) -> GdkPixbuf.Pixbuf:
            self._remote_icon_by_endpoint[endpoint] = pixbuf
            if sip:
                self._remote_icon_by_host_ip[sip] = pixbuf
                if isinstance(binding_disk_key, str) and binding_disk_key.strip():
                    bk = binding_disk_key.strip()
                    if bk.startswith(("http://", "https://")):
                        self._persist_remote_icon_index_entry(sip, bk)
            return pixbuf

        if sip:
            self._ensure_remote_icon_index()
            entry = self._remote_icon_index.get(sip)
            if isinstance(entry, dict):
                sha = entry.get("payload_sha256")
                if isinstance(sha, str) and len(sha) == 64:
                    sl = sha.lower()
                    if all(c in "0123456789abcdef" for c in sl):
                        pl = self._remote_icon_disk_dir / f"{sl}.payload"
                        if pl.is_file():
                            pb = self._pixbuf_from_image_bytes(pl.read_bytes())
                            if pb is not None:
                                scaled = self._normalize_icon_pixbuf(pb, size)
                                bind_key = entry.get("canonical_url")
                                if not isinstance(bind_key, str) or not bind_key.strip():
                                    bind_key = None
                                return _register_hit(scaled, bind_key)

        disk_keys = self._iter_remote_icon_disk_keys(device)

        if not disk_keys:
            return self._remote_icon_by_endpoint.get(endpoint)

        for lk in disk_keys:
            cached = self._remote_icon_cache.get(lk)
            if cached is not None:
                normalized = self._normalize_icon_pixbuf(cached, size)
                pair = self._primary_remote_icon_fetch_pair(device)
                if pair:
                    self._remote_icon_cache.setdefault(pair[1], normalized)
                return _register_hit(normalized, lk)

        for lk in disk_keys:
            disk_pixbuf = self._load_remote_icon_from_disk(lk, size)
            if disk_pixbuf is not None:
                self._remote_icon_cache[lk] = disk_pixbuf
                pair = self._primary_remote_icon_fetch_pair(device)
                if pair:
                    self._remote_icon_cache.setdefault(pair[1], disk_pixbuf)
                return _register_hit(disk_pixbuf, lk)

        pair = self._primary_remote_icon_fetch_pair(device)
        if pair is None:
            return self._remote_icon_by_endpoint.get(endpoint)
        fetch_url, cache_key = pair
        self._start_remote_icon_fetch(fetch_url, cache_key, endpoint, size, sip=sip)
        return self._remote_icon_by_endpoint.get(endpoint)

    def _remote_icon_fetch_pending(self, device: Device) -> bool:
        pair = self._primary_remote_icon_fetch_pair(device)
        if pair is None:
            return False
        _fetch_url, cache_key = pair
        return cache_key in self._remote_icon_fetching

    def _bundle_has_remote_icon_fetch_pending(self, bundle: _DeviceBundle) -> bool:
        """True while a background HTTP fetch is loading a sharper icon for any row in the bundle."""
        for d in bundle.devices:
            if self._remote_icon_fetch_pending(d):
                return True
        return False

    @staticmethod
    def _remote_icon_digest(icon_url: str) -> str:
        return hashlib.sha256(icon_url.encode("utf-8")).hexdigest()

    def _remote_icon_payload_path(self, payload_sha256: str) -> Path:
        """Path helper for indexed payload SHA entries."""
        sha = str(payload_sha256).strip().lower()
        return self._remote_icon_disk_dir / f"{sha}.payload"

    def _remote_icon_disk_path_payload(self, icon_url: str) -> Path:
        return self._remote_icon_disk_dir / f"{self._remote_icon_digest(icon_url)}.payload"

    @staticmethod
    def _pixbuf_from_image_bytes(data: bytes) -> GdkPixbuf.Pixbuf | None:
        try:
            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            return loader.get_pixbuf()
        except Exception:
            return None

    def _load_remote_icon_from_disk(self, icon_url: str, size: int) -> GdkPixbuf.Pixbuf | None:
        payload_path = self._remote_icon_disk_path_payload(icon_url)
        if payload_path.is_file():
            pixbuf = self._pixbuf_from_image_bytes(payload_path.read_bytes())
            if pixbuf is not None:
                return self._normalize_icon_pixbuf(pixbuf, size)
            try:
                payload_path.unlink()
            except OSError:
                pass
        return None

    def _save_remote_icon_payload(self, icon_url: str, data: bytes) -> None:
        if not data:
            return
        path = self._remote_icon_disk_path_payload(icon_url)
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except Exception:
            try:
                if tmp.is_file():
                    tmp.unlink()
            except OSError:
                pass

    def _start_remote_icon_fetch(
        self, fetch_url: str, cache_key: str, endpoint: tuple[str, int], size: int, sip: str = ""
    ) -> None:
        if cache_key in self._remote_icon_fetching:
            return
        self._remote_icon_fetching.add(cache_key)

        def _worker() -> None:
            try:
                with self._urlopen_remote_icon(fetch_url) as response:
                    data = response.read()
                self._save_remote_icon_payload(cache_key, data)
                pixbuf = self._pixbuf_from_image_bytes(data)
                if pixbuf is not None:
                    scaled = self._normalize_icon_pixbuf(pixbuf, size)
                    if scaled is not None:
                        endpoint_c = (endpoint[0], int(endpoint[1]))
                        ck = cache_key
                        sip_bind = sip.strip()
                        from gi.repository import GLib

                        def _commit_fetch() -> bool:
                            self._remote_icon_cache[ck] = scaled
                            self._remote_icon_by_endpoint[endpoint_c] = scaled
                            if sip_bind:
                                self._remote_icon_by_host_ip[sip_bind] = scaled
                                self._persist_remote_icon_index_entry(sip_bind, ck)
                            self._rebuild_icon_sections(self._filtered_devices)
                            return False

                        GLib.idle_add(_commit_fetch)
            except Exception as exc:
                _logger.debug("Remote icon fetch failed url=%s: %s", fetch_url, exc)
            finally:
                self._remote_icon_fetching.discard(cache_key)

        threading.Thread(target=_worker, daemon=True).start()

    def _refresh_icons_after_async_fetch(self) -> bool:
        self._rebuild_icon_sections(self._filtered_devices)
        return False

    def _normalize_icon_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf, size: int) -> GdkPixbuf.Pixbuf:
        if size <= 0:
            return pixbuf
        source = pixbuf
        if pixbuf.get_has_alpha():
            cropped = self._trim_transparent_borders(pixbuf)
            if cropped is not None:
                source = cropped
        width = max(1, int(source.get_width()))
        height = max(1, int(source.get_height()))
        # Keep original size when dimensions straddle the target
        # (one side below 64, the other above 64).
        if (width > size and height < size) or (height > size and width < size):
            return source
        # Preserve aspect ratio while prioritizing visual height consistency:
        # make icon height = target size, let width adapt.
        ratio = size / height
        target_w = max(1, int(width * ratio))
        target_h = max(1, int(height * ratio))
        scaled = source.scale_simple(target_w, target_h, GdkPixbuf.InterpType.BILINEAR)
        return scaled if scaled is not None else source

    def _trim_transparent_borders(self, pixbuf: GdkPixbuf.Pixbuf) -> GdkPixbuf.Pixbuf | None:
        if not pixbuf.get_has_alpha():
            return None
        width = int(pixbuf.get_width())
        height = int(pixbuf.get_height())
        channels = int(pixbuf.get_n_channels())
        rowstride = int(pixbuf.get_rowstride())
        pixels = memoryview(pixbuf.get_pixels())
        min_x = width
        min_y = height
        max_x = -1
        max_y = -1
        alpha_index = channels - 1

        for y in range(height):
            row_start = y * rowstride
            for x in range(width):
                index = row_start + (x * channels) + alpha_index
                if pixels[index] > 8:
                    if x < min_x:
                        min_x = x
                    if y < min_y:
                        min_y = y
                    if x > max_x:
                        max_x = x
                    if y > max_y:
                        max_y = y

        if max_x < min_x or max_y < min_y:
            return None
        crop_w = (max_x - min_x) + 1
        crop_h = (max_y - min_y) + 1
        if crop_w == width and crop_h == height:
            return None
        try:
            return pixbuf.new_subpixbuf(min_x, min_y, crop_w, crop_h)
        except Exception:
            return None

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"""
            .offline-device-monitored {
                opacity: 0.45;
            }
            .nn-mdns-service-header {
                padding-top: 4px;
                padding-bottom: 4px;
            }
            .nn-mdns-service-body {
                padding-bottom: 6px;
            }
            @keyframes nn-provisional-pulse {
                0% { background-color: rgba(46, 110, 210, 0.11); }
                50% { background-color: rgba(46, 110, 210, 0.26); }
                100% { background-color: rgba(46, 110, 210, 0.11); }
            }
            .nn-identity-provisional {
                border-radius: 10px;
                padding: 6px 6px 4px 6px;
                animation: nn-provisional-pulse 2.1s ease-in-out infinite;
            }
            """
        )
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(
                screen,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

    def _add_column(self, title: str, model_index: int) -> None:
        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn(title, renderer)
        column.set_resizable(True)
        column.set_cell_data_func(renderer, self._cell_data_func, model_index)
        self._tree.append_column(column)

    def _list_pulse_provisional_identity_tick(self) -> bool:
        """Drive a soft background pulse on list rows with resolving identity and/or loading icons."""
        self._provisional_pulse_phase += 0.38
        devs = getattr(self, "_filtered_devices", None)
        if not devs or not any(
            _bundle_identity_provisional(b) or self._bundle_has_remote_icon_fetch_pending(b) for b in devs
        ):
            return True
        it = self._list_store.get_iter_first()
        while it is not None:
            bundle = self._list_store.get_value(it, 0)
            if isinstance(bundle, _DeviceBundle) and (
                _bundle_identity_provisional(bundle)
                or self._bundle_has_remote_icon_fetch_pending(bundle)
            ):
                self._list_store.row_changed(self._list_store.get_path(it), it)
            it = self._list_store.iter_next(it)
        return True

    def _cell_data_func(self, column: Gtk.TreeViewColumn, cell: Gtk.CellRendererText, model, iter_, data) -> None:
        model_index = int(data)
        bundle = model.get_value(iter_, 0)
        value = model.get_value(iter_, model_index)
        cell.set_property("text", str(value) if value is not None else "")
        monitored = bool(getattr(bundle, "monitored", False))
        online = bool(getattr(bundle, "online", True))
        prov = isinstance(bundle, _DeviceBundle) and _bundle_identity_provisional(bundle)
        icon_ld = isinstance(bundle, _DeviceBundle) and self._bundle_has_remote_icon_fetch_pending(bundle)
        pulse_row = prov or icon_ld
        if (not online) and monitored:
            cell.set_property("foreground-set", True)
            cell.set_property("foreground-rgba", self._offline_fg)
            cell.set_property("style-set", False)
            cell.set_property("cell-background-set", False)
        elif pulse_row:
            alpha = 0.06 + 0.12 * (0.5 + 0.5 * math.sin(self._provisional_pulse_phase))
            cell.set_property("cell-background-rgba", Gdk.RGBA(0.22, 0.48, 0.92, alpha))
            cell.set_property("cell-background-set", True)
            if prov:
                cell.set_property("foreground-set", True)
                cell.set_property("foreground-rgba", self._provisional_fg)
                cell.set_property("style-set", True)
                cell.set_property("style", Pango.Style.ITALIC)
            else:
                try:
                    cell.set_property("foreground-set", False)
                except Exception:
                    pass
                cell.set_property("style-set", False)
        else:
            try:
                cell.set_property("foreground-set", False)
            except Exception:
                pass
            cell.set_property("style-set", False)
            cell.set_property("cell-background-set", False)

    def _build_device_bundles(self, devices: list[Device]) -> list[_DeviceBundle]:
        bundles_by_id: dict[str, _DeviceBundle] = {}
        host_to_bundle_id: dict[str, str] = {}
        order: list[str] = []
        for device in devices:
            endpoint = (device.ip, device.port)
            bundle_id = f"endpoint:{endpoint[0]}:{endpoint[1]}"
            bundle_keys = self._device_host_bundle_keys(device)
            for key in bundle_keys:
                existing = host_to_bundle_id.get(key)
                if existing is not None:
                    bundle_id = existing
                    break
            bundle = bundles_by_id.get(bundle_id)
            if bundle is None:
                bundle = _DeviceBundle(ip=device.ip, port=device.port, primary=device)
                bundles_by_id[bundle_id] = bundle
                order.append(bundle_id)

            if device.source == "mdns":
                bundle.mdns_device = device
            elif device.source == "ssdp":
                bundle.ssdp_device = device
            elif device.source == "wsd":
                bundle.wsd_device = device
            elif device.source == "wsdd":
                bundle.wsdd_device = device
            elif device.source == "nmb":
                bundle.nmb_device = device
            bundle.primary = self._choose_bundle_primary(bundle)

            _logger.debug(
                "bundle_merge attach source=%s endpoint=%s:%s merge_keys=%s name=%r slots="
                "mdns=%s ssdp=%s wsdd=%s wsd=%s nmb=%s",
                device.source,
                device.ip,
                device.port,
                bundle_keys,
                device.name,
                bundle.mdns_device is not None,
                bundle.ssdp_device is not None,
                bundle.wsdd_device is not None,
                bundle.wsd_device is not None,
                bundle.nmb_device is not None,
            )

            for key in bundle_keys:
                host_to_bundle_id[key] = bundle_id
        ordered = [bundles_by_id[key] for key in order]
        for bundle in ordered:
            # Prefer primary row IP:port for display and prefs (`ip:port` icon keys align with SSDP/http).
            bundle.ip = bundle.primary.ip
            bundle.port = int(bundle.primary.port)
        return ordered

    def _protocol_row_preference_rank(self, device: Device | None) -> int:
        """Lower = stronger (see ``merge.information_precedence`` in ``discovery.json``)."""
        if device is None:
            return 99
        role = information_precedence_role_for_device_source(device.source or "")
        return information_precedence_rank(self._information_precedence, role)

    def _choose_bundle_primary(self, bundle: _DeviceBundle) -> Device:
        """Pick list/detail primary row using ``merge.information_precedence`` across live protocol rows."""
        candidates: list[Device] = []
        for d in (bundle.ssdp_device, bundle.wsdd_device, bundle.wsd_device, bundle.nmb_device, bundle.mdns_device):
            if d is not None:
                candidates.append(d)
        if not candidates:
            return bundle.primary
        if len(candidates) == 1:
            _logger.debug(
                "bundle_primary single_candidate source=%s ip=%s name=%r",
                candidates[0].source,
                candidates[0].ip,
                candidates[0].name,
            )
            return candidates[0]

        def _sort_key(d: Device) -> tuple[int, int]:
            src = (d.source or "").strip().lower()
            return (
                self._protocol_row_preference_rank(d),
                _SOURCE_PRIMARY_TIEBREAK.get(src, 9),
            )

        ip_hint = bundle.primary.ip if bundle.primary else ""
        _logger.debug(
            "bundle_primary start ip=%r merge.information_precedence=%s slack=%s",
            ip_hint,
            self._information_precedence,
            _PRIMARY_RANK_SLACK_FOR_NAME,
        )

        ordered = sorted(candidates, key=_sort_key)
        for i, d in enumerate(ordered):
            src = (d.source or "").strip().lower()
            rk = self._protocol_row_preference_rank(d)
            role = information_precedence_role_for_device_source(d.source or "")
            tie = _SOURCE_PRIMARY_TIEBREAK.get(src, 9)
            wk = _weak_bundle_display_name(d)
            _logger.debug(
                "bundle_primary sorted[%d] %s",
                i,
                _bundle_row_debug_line(d, rank=rk, role=role, tie=tie, weak=wk),
            )

        top = ordered[0]
        # NetBIOS machine name wins over anonymous WSD/wsdd scopes even when merge ranks WSD higher.
        if bundle.nmb_device is not None and not _weak_bundle_display_name(bundle.nmb_device):
            if top.source in {"wsd", "wsdd"} and _weak_bundle_display_name(top):
                _logger.debug(
                    "bundle_primary choice=nmb_override (NetBIOS hostname over weak %s) "
                    "nmb_name=%r top_was=%s top_weak=%s",
                    top.source,
                    bundle.nmb_device.name,
                    top.name,
                    _weak_bundle_display_name(top),
                )
                return bundle.nmb_device
            _logger.debug(
                "bundle_primary nmb_override skipped nmb_weak=%s top_source=%s top_weak=%s",
                _weak_bundle_display_name(bundle.nmb_device),
                top.source,
                _weak_bundle_display_name(top),
            )
        elif bundle.nmb_device is None:
            _logger.debug(
                "bundle_primary no nmb_device in bundle (WSD/mDNS alone?) ip_hint=%r — "
                "check discovery nmb + directed_ips",
                ip_hint,
            )

        best_rank = self._protocol_row_preference_rank(ordered[0])
        slack = _PRIMARY_RANK_SLACK_FOR_NAME
        _logger.debug(
            "bundle_primary slack_scan best_rank_index=%s max_rank=%s",
            best_rank,
            best_rank + slack,
        )
        for d in ordered:
            dr = self._protocol_row_preference_rank(d)
            if dr > best_rank + slack:
                _logger.debug(
                    "bundle_primary slack_scan stop past_slack candidate=%s dr=%s",
                    d.source,
                    dr,
                )
                break
            if not _weak_bundle_display_name(d):
                _logger.debug(
                    "bundle_primary choice=slack_first_strong_name source=%s name=%r",
                    d.source,
                    d.name,
                )
                return d
        chosen = ordered[0]
        _logger.debug(
            "bundle_primary choice=fallback_sorted_first source=%s name=%r weak=%s",
            chosen.source,
            chosen.name,
            _weak_bundle_display_name(chosen),
        )
        return chosen

    def _device_host_bundle_keys(self, device: Device) -> list[str]:
        """Keys that may refer to the same physical host (cross-protocol merging).

        A Synology SSDP row often exposes a UUID (UDN/USN); `_ftp._tcp` on the same box may only align
        on MAC or LAN IP. Register every available key so the same bundle aggregates both sources.
        """
        if device.source not in {"mdns", "ssdp", "wsd", "wsdd", "nmb"}:
            return []
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        ordered: list[str] = []
        mac = self._mac_for_bundle_merge(device, metadata)
        if mac:
            ordered.append(f"mac:{mac}")
        uid = self._extract_uid(metadata)
        if uid:
            ordered.append(f"uid:{uid}")
        ip = str(device.ip).strip()
        if ip and ip != "0.0.0.0":
            ordered.append(f"ip:{ip}")
        seen: set[str] = set()
        deduped: list[str] = []
        for key in ordered:
            if key not in seen:
                seen.add(key)
                deduped.append(key)
        return deduped

    def _extract_uid(self, metadata: dict) -> str:
        if not isinstance(metadata, dict):
            return ""
        xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
        txt_fields = metadata.get("txt") if isinstance(metadata.get("txt"), dict) else {}

        usn = metadata.get("usn")
        if isinstance(usn, str) and usn.strip():
            u = uuid_urn_if_present(usn)
            if u:
                return u
            return usn.strip().lower().split("::", 1)[0]

        wsd_epr = metadata.get("wsd_epr")
        if isinstance(wsd_epr, str) and wsd_epr.strip():
            u = uuid_urn_if_present(wsd_epr)
            if u:
                return u
        wsdd_uri = metadata.get("wsdd_uri")
        if isinstance(wsdd_uri, str) and wsdd_uri.strip():
            u = uuid_urn_if_present(wsdd_uri)
            if u:
                return u

        udn_raw = xml_fields.get("UDN")
        if isinstance(udn_raw, str) and udn_raw.strip():
            u = uuid_urn_if_present(udn_raw)
            if u:
                return u

        candidates = [
            xml_fields.get("UDN"),
            metadata.get("udn"),
            txt_fields.get("uuid"),
            txt_fields.get("udn"),
            txt_fields.get("id"),
            txt_fields.get("deviceid"),
            txt_fields.get("device_id"),
            txt_fields.get("serial"),
            txt_fields.get("serialnumber"),
        ]
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        return ""

    def _extract_mac(self, metadata: dict) -> str:
        if not isinstance(metadata, dict):
            return ""
        xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
        txt_fields = metadata.get("txt") if isinstance(metadata.get("txt"), dict) else {}
        candidates = [
            xml_fields.get("mac"),
            metadata.get("mac"),
            metadata.get("mac_address"),
            metadata.get("MAC"),
            metadata.get("macAddress"),
            txt_fields.get("mac"),
            txt_fields.get("macaddress"),
            txt_fields.get("mac_address"),
        ]
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        return ""

    @staticmethod
    def _normalize_mac_for_bundle_merge(raw: str) -> str:
        """Six-octet lowercase MAC with colons; invalid tokens yield ''."""
        t = (raw or "").strip().lower().replace("-", ":")
        parts = t.split(":")
        if len(parts) != 6:
            return ""
        if not all(len(p) == 2 and all(c in "0123456789abcdef" for c in p) for p in parts):
            return ""
        return ":".join(parts)

    def _mac_for_bundle_merge(self, device: Device, metadata: dict) -> str:
        """MAC from metadata, else kernel ARP/IPv6 neighbor cache — aligns IPv4 vs IPv6 rows for the same host."""
        m = self._extract_mac(metadata)
        n = self._normalize_mac_for_bundle_merge(m) if m else ""
        if n:
            return n
        hit = lookup_mac_from_neighbor_cache(str(getattr(device, "ip", "") or "").strip())
        return self._normalize_mac_for_bundle_merge(hit) if hit else ""
