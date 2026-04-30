"""Device view supporting list and icon modes."""

import gi
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import threading
from urllib.request import urlopen

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk
from collections.abc import Callable

from model.device import Device
from ui.device_details import DeviceDetailsDialog
from ui.icons import resolve_icon_path
from utils.browser import open_url
from utils.details_payload import build_mdns_payload, build_ssdp_payload


@dataclass(slots=True)
class _DeviceBundle:
    ip: str
    port: int
    primary: Device
    ssdp_device: Device | None = None
    mdns_device: Device | None = None

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
    def display_sources(self) -> str:
        labels: list[str] = []
        if self.mdns_device is not None:
            labels.append("mDNS")
        if self.ssdp_device is not None:
            labels.append("SSDP")
        return ", ".join(labels) if labels else self.primary.source.upper()

    @property
    def badges(self) -> list[tuple[str, Gtk.Align]]:
        return []

    @property
    def open_url(self) -> str | None:
        for candidate in (self.mdns_device, self.ssdp_device, self.primary):
            if candidate is not None and candidate.url:
                return candidate.url
        return None

    @property
    def devices(self) -> list[Device]:
        unique: dict[str, Device] = {}
        for device in (self.mdns_device, self.ssdp_device, self.primary):
            if device is not None:
                unique[device.key] = device
        return list(unique.values())


class DeviceList(Gtk.Box):
    def __init__(
        self,
        parent_window: Gtk.Window | None = None,
        on_set_monitored: Callable[[Device, bool], None] | None = None,
        on_icon_mode_changed: Callable[[], None] | None = None,
        on_set_type_override: Callable[[str, str, int, str | None], None] | None = None,
        on_set_name_override: Callable[[str, str, int, str | None], None] | None = None,
        on_set_location_override: Callable[[str, str, int, str | None], None] | None = None,
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
        self._location_options: list[str] = []
        self._icon_source_overrides: dict[tuple[str, int], str] = {}
        self._custom_icon_overrides: dict[tuple[str, int], str] = {}
        self._remote_icon_cache: dict[str, GdkPixbuf.Pixbuf] = {}
        self._remote_icon_by_endpoint: dict[tuple[str, int], GdkPixbuf.Pixbuf] = {}
        self._remote_icon_fetching: set[str] = set()
        self._custom_icons_dir = Path.home() / ".config" / "netneighbor" / "custom_icons"
        self._builtin_icons_dir = Path(__file__).resolve().parent.parent / "assets" / "icons"
        self._install_css()

        self._list_store = Gtk.ListStore(object, str, str, int, str, str)
        self._tree = Gtk.TreeView(model=self._list_store)
        self._offline_fg = Gdk.RGBA(0.45, 0.45, 0.45, 1.0)
        self._add_column("Name", 1)
        self._add_column("IP", 2)
        self._add_column("Port", 3)
        self._add_column("Category", 4)
        self._add_column("Source", 5)
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

    def set_devices(self, devices: list[Device]) -> None:
        self._devices = devices
        bundles = self._build_device_bundles(devices)
        self._filtered_devices = self._apply_category_filter(bundles)
        self._list_store.clear()
        for bundle in self._filtered_devices:
            self._list_store.append([bundle, bundle.name, bundle.ip, bundle.port, bundle.category, bundle.display_sources])
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
            grouped_devices[bundle.category].append(bundle)

        for category in sorted(grouped_devices.keys()):
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
                return location.strip()
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
        icon_overlay.set_size_request(64, 64)
        icon_overlay.set_halign(Gtk.Align.CENTER)
        icon_overlay.set_valign(Gtk.Align.CENTER)
        image = Gtk.Image.new_from_pixbuf(self._load_device_icon(bundle.primary))
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
        if (not bundle.online) and bundle.monitored:
            container.get_style_context().add_class("offline-device-monitored")
        container.set_tooltip_text(f"IP: {bundle.ip}")
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

        open_item = Gtk.MenuItem.new_with_label(_("Open"))
        open_item.connect("activate", self._on_open_item_activate, bundle)
        open_item.set_sensitive(bool(bundle.open_url))
        menu.append(open_item)

        if self._has_ssdp_details(bundle):
            ssdp_item = Gtk.MenuItem.new_with_label(_("SSDP details"))
            ssdp_item.connect("activate", self._on_show_ssdp_xml_activate, bundle)
            menu.append(ssdp_item)

        if self._has_mdns_details(bundle):
            mdns_item = Gtk.MenuItem.new_with_label(_("mDNS details"))
            mdns_item.connect("activate", self._on_show_mdns_txt_activate, bundle)
            menu.append(mdns_item)
        icon_item = Gtk.MenuItem.new_with_label(_("Icon..."))
        icon_item.connect("activate", self._on_icon_item_activate, bundle)
        icon_item.set_sensitive(self._has_ssdp_details(bundle) or self._has_mdns_details(bundle))
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
        location_item.set_sensitive(self._on_set_location_override is not None and bool(self._location_options))
        for location_value in self._location_options:
            loc_choice_item = Gtk.MenuItem.new_with_label(location_value)
            loc_choice_item.connect("activate", self._on_location_item_activate, bundle, location_value)
            location_menu.append(loc_choice_item)
        clear_location_item = Gtk.MenuItem.new_with_label(_("Clear location"))
        clear_location_item.connect("activate", self._on_location_item_activate, bundle, None)
        location_menu.append(Gtk.SeparatorMenuItem())
        location_menu.append(clear_location_item)
        menu.append(location_item)

        type_item = Gtk.MenuItem.new_with_label(_("Device type"))
        type_menu = Gtk.Menu()
        type_item.set_submenu(type_menu)
        menu.append(type_item)
        current_type = bundle.primary.type.strip().lower() if isinstance(bundle.primary.type, str) else "unknown"
        type_auto_item = Gtk.RadioMenuItem.new_with_label(None, _("Auto"))
        type_nas_item = Gtk.RadioMenuItem.new_with_label_from_widget(type_auto_item, _("NAS"))
        type_computer_item = Gtk.RadioMenuItem.new_with_label_from_widget(type_auto_item, _("Computer"))
        type_router_item = Gtk.RadioMenuItem.new_with_label_from_widget(type_auto_item, _("Router"))
        type_media_item = Gtk.RadioMenuItem.new_with_label_from_widget(type_auto_item, _("Media server"))
        type_printer_item = Gtk.RadioMenuItem.new_with_label_from_widget(type_auto_item, _("Printer"))
        type_network_printer_item = Gtk.RadioMenuItem.new_with_label_from_widget(type_auto_item, _("Network Printer"))
        type_smart_speaker_item = Gtk.RadioMenuItem.new_with_label_from_widget(type_auto_item, _("SmartSpeaker"))
        type_smart_tv_item = Gtk.RadioMenuItem.new_with_label_from_widget(type_auto_item, _("SmartTV"))
        type_smart_device_item = Gtk.RadioMenuItem.new_with_label_from_widget(type_auto_item, _("SmartDevice"))
        type_camera_item = Gtk.RadioMenuItem.new_with_label_from_widget(type_auto_item, _("Camera"))
        type_home_appliance_item = Gtk.RadioMenuItem.new_with_label_from_widget(type_auto_item, _("HomeAppliance"))
        type_cnc_item = Gtk.RadioMenuItem.new_with_label_from_widget(type_auto_item, _("CNC"))
        type_3d_printer_item = Gtk.RadioMenuItem.new_with_label_from_widget(type_auto_item, _("3D printer"))
        type_auto_item.connect("toggled", self._on_type_item_toggled, bundle, None)
        type_nas_item.connect("toggled", self._on_type_item_toggled, bundle, "nas")
        type_computer_item.connect("toggled", self._on_type_item_toggled, bundle, "computer")
        type_router_item.connect("toggled", self._on_type_item_toggled, bundle, "router")
        type_media_item.connect("toggled", self._on_type_item_toggled, bundle, "mediaserver")
        type_printer_item.connect("toggled", self._on_type_item_toggled, bundle, "printer")
        type_network_printer_item.connect("toggled", self._on_type_item_toggled, bundle, "networkprinter")
        type_smart_speaker_item.connect("toggled", self._on_type_item_toggled, bundle, "smartspeaker")
        type_smart_tv_item.connect("toggled", self._on_type_item_toggled, bundle, "smarttv")
        type_smart_device_item.connect("toggled", self._on_type_item_toggled, bundle, "smartdevice")
        type_camera_item.connect("toggled", self._on_type_item_toggled, bundle, "camera")
        type_home_appliance_item.connect("toggled", self._on_type_item_toggled, bundle, "homeappliance")
        type_cnc_item.connect("toggled", self._on_type_item_toggled, bundle, "cnc")
        type_3d_printer_item.connect("toggled", self._on_type_item_toggled, bundle, "3dprinter")
        type_menu.append(type_auto_item)
        type_menu.append(type_nas_item)
        type_menu.append(type_computer_item)
        type_menu.append(type_router_item)
        type_menu.append(type_media_item)
        type_menu.append(type_printer_item)
        type_menu.append(type_network_printer_item)
        type_menu.append(type_smart_speaker_item)
        type_menu.append(type_smart_tv_item)
        type_menu.append(type_smart_device_item)
        type_menu.append(type_camera_item)
        type_menu.append(type_home_appliance_item)
        type_menu.append(type_cnc_item)
        type_menu.append(type_3d_printer_item)
        if current_type == "nas":
            type_nas_item.set_active(True)
        elif current_type == "computer":
            type_computer_item.set_active(True)
        elif current_type == "router":
            type_router_item.set_active(True)
        elif current_type == "mediaserver":
            type_media_item.set_active(True)
        elif current_type == "printer":
            type_printer_item.set_active(True)
        elif current_type == "networkprinter":
            type_network_printer_item.set_active(True)
        elif current_type == "smartspeaker":
            type_smart_speaker_item.set_active(True)
        elif current_type == "smarttv":
            type_smart_tv_item.set_active(True)
        elif current_type == "smartdevice":
            type_smart_device_item.set_active(True)
        elif current_type == "camera":
            type_camera_item.set_active(True)
        elif current_type == "homeappliance":
            type_home_appliance_item.set_active(True)
        elif current_type == "cnc":
            type_cnc_item.set_active(True)
        elif current_type == "3dprinter":
            type_3d_printer_item.set_active(True)
        else:
            type_auto_item.set_active(True)

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
            if mode != "custom":
                self._custom_icon_overrides.pop(endpoint, None)
            elif custom_icon_name:
                self._custom_icon_overrides[endpoint] = custom_icon_name
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
            self._on_set_name_override(device.source, bundle.ip, bundle.port, new_name)

    def _on_location_item_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle, value: str | None) -> None:
        if self._on_set_location_override is None:
            return
        for device in bundle.devices:
            self._on_set_location_override(device.source, bundle.ip, bundle.port, value)

    def _on_type_item_toggled(self, menu_item: Gtk.RadioMenuItem, bundle: _DeviceBundle, device_type: str | None) -> None:
        if not menu_item.get_active():
            return
        if self._on_set_type_override is None:
            return
        self._on_set_type_override(bundle.primary.source, bundle.ip, bundle.port, device_type)

    def _on_open_item_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle) -> None:
        self._open_device(bundle)

    def _on_show_ssdp_xml_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle) -> None:
        self._open_ssdp_details(bundle)

    def _open_ssdp_details(self, bundle: _DeviceBundle, initial_tab: str | None = None) -> None:
        device = bundle.ssdp_device
        if device is None:
            return
        fields, raw_xml, services_list, troubleshooting_fields, xml_location = build_ssdp_payload(device)
        self._show_details_dialog(
            title=f"{_('SSDP details')} - {bundle.name}",
            fields=fields,
            raw_content=raw_xml,
            raw_button_label=_("Show raw XML"),
            services_list=None,
            troubleshooting_records=troubleshooting_fields,
            raw_xml_location=xml_location,
            icon_mode=self._normalized_icon_mode(bundle),
            selected_icon_id=self._custom_icon_overrides.get((bundle.ip, bundle.port)),
            icon_choices=self._load_icon_choices(bundle.primary.type),
            on_apply_icon_settings=lambda mode, custom: self._apply_bundle_icon_settings(bundle, mode, custom),
            custom_icons_dir=str(self._custom_icons_dir),
            initial_tab=initial_tab,
            has_device_icon_source=self._bundle_has_ssdp_icon_source(bundle),
        )

    def _on_show_mdns_txt_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle) -> None:
        self._open_mdns_details(bundle)

    def _on_icon_item_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle) -> None:
        if bundle.mdns_device is not None:
            self._open_mdns_details(bundle, initial_tab="appearance")
            return
        if bundle.ssdp_device is not None:
            self._open_ssdp_details(bundle, initial_tab="appearance")

    def _open_mdns_details(self, bundle: _DeviceBundle, initial_tab: str | None = None) -> None:
        device = bundle.mdns_device
        if device is None:
            return
        fields, txt_records, services_list = build_mdns_payload(device)
        self._show_details_dialog(
            title=f"{_('mDNS details')} - {bundle.name}",
            fields=fields,
            txt_records=txt_records,
            services_list=services_list,
            icon_mode=self._normalized_icon_mode(bundle),
            selected_icon_id=self._custom_icon_overrides.get((bundle.ip, bundle.port)),
            icon_choices=self._load_icon_choices(bundle.primary.type),
            on_apply_icon_settings=lambda mode, custom: self._apply_bundle_icon_settings(bundle, mode, custom),
            custom_icons_dir=str(self._custom_icons_dir),
            initial_tab=initial_tab,
            has_device_icon_source=False,
        )

    def _open_device(self, bundle: _DeviceBundle) -> None:
        # Double-click behavior priority:
        # 1) presentation URL (open browser)
        # 2) SSDP details when no presentation URL
        # 3) mDNS details
        # 4) no action
        url = bundle.open_url
        if url:
            open_url(url)
            return
        if bundle.ssdp_device is not None:
            self._open_ssdp_details(bundle)
            return
        if bundle.mdns_device is not None:
            self._open_mdns_details(bundle)

    def _show_details_dialog(
        self,
        title: str,
        fields: list[tuple[str, str]],
        services_list: list[tuple[str, str, str]] | None = None,
        txt_records: list[tuple[str, str]] | None = None,
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
    ) -> None:
        parent = self._parent_window
        if parent is None:
            top_level = self.get_toplevel()
            if isinstance(top_level, Gtk.Window):
                parent = top_level
        if parent is None:
            return
        dialog = DeviceDetailsDialog(
            parent=parent,
            title=title,
            fields=fields,
            services_records=services_list,
            txt_records=txt_records,
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
        )
        dialog.run()
        dialog.destroy()

    def _has_ssdp_details(self, bundle: _DeviceBundle) -> bool:
        return bundle.ssdp_device is not None

    def _has_mdns_details(self, bundle: _DeviceBundle) -> bool:
        return bundle.mdns_device is not None

    def _bundle_has_ssdp_icon_source(self, bundle: _DeviceBundle) -> bool:
        device = bundle.ssdp_device
        if device is None:
            return False
        if isinstance(device.icon, str) and device.icon.strip():
            return True
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
        icon_url = xml_fields.get("iconURL")
        return isinstance(icon_url, str) and icon_url.strip() != ""

    def _apply_category_filter(self, bundles: list[_DeviceBundle]) -> list[_DeviceBundle]:
        if not self._category_filter:
            return list(bundles)
        return [bundle for bundle in bundles if bundle.category == self._category_filter]

    def _on_tree_row_activated(self, _tree: Gtk.TreeView, path: Gtk.TreePath, _column: Gtk.TreeViewColumn) -> None:
        model = self._tree.get_model()
        tree_iter = model.get_iter(path)
        bundle = model.get_value(tree_iter, 0)
        if isinstance(bundle, _DeviceBundle):
            self._open_device(bundle)

    def _on_tree_button_press(self, tree: Gtk.TreeView, event: Gdk.EventButton) -> bool:
        if event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS:
            hit = tree.get_path_at_pos(int(event.x), int(event.y))
            if hit is None:
                return False
            path, _column, _x, _y = hit
            model = tree.get_model()
            tree_iter = model.get_iter(path)
            bundle = model.get_value(tree_iter, 0)
            if isinstance(bundle, _DeviceBundle):
                self._open_device(bundle)
                return True
            return False

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

    def _load_device_icon(self, device: Device) -> GdkPixbuf.Pixbuf:
        endpoint = (device.ip, device.port)
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
            remote_icon = self._load_remote_icon(device, 64)
            if remote_icon is not None:
                return remote_icon
            icon_path = resolve_icon_path(device.icon)
            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_size(str(icon_path), 64, 64)
            except Exception:
                pass

        theme = Gtk.IconTheme.get_default()
        fallback_by_type = {
            "esp32": ["cpu", "application-x-firmware", "network-wireless"],
            "router": ["network-wireless-router", "network-server", "network-workgroup"],
            "mediaserver": ["multimedia-player", "folder-videos", "network-server"],
            "printer": ["printer-network", "printer", "network-server"],
            "networkprinter": ["printer-network", "printer", "network-server"],
            "smartspeaker": ["audio-speakers", "multimedia-player", "network-server"],
            "smarttv": ["video-display", "multimedia-player", "network-server"],
            "smartdevice": ["applications-system", "network-server", "computer"],
            "camera": ["camera-web", "camera-photo", "network-server"],
            "homeappliance": ["applications-utilities", "network-server", "computer"],
            "cnc": ["applications-engineering", "applications-system", "network-server"],
            "3dprinter": ["printer-3d", "printer-network", "printer"],
            "nas": ["drive-harddisk", "folder-remote", "network-server"],
            "computer": ["computer", "network-workgroup", "video-display"],
            "http": ["applications-internet", "web-browser", "network-server"],
            "unknown": ["network-workgroup", "network-server", "computer", "folder"],
        }
        fallback_names = fallback_by_type.get(device.type, fallback_by_type["unknown"])
        for icon_name in fallback_names:
            try:
                return theme.load_icon(icon_name, 64, 0)
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
            if self._builtin_icons_dir.exists():
                for icon_path in sorted(self._builtin_icons_dir.glob("*.png")):
                    if icon_path.name == "logo.png":
                        continue
                    try:
                        preview = GdkPixbuf.Pixbuf.new_from_file_at_size(str(icon_path), 64, 64)
                    except Exception:
                        continue
                    icon_id = f"builtin:{icon_path.name}"
                    label = f"{icon_path.name} ({_('Built-in')})"
                    choices.append((icon_id, label, preview))
            if not self._custom_icons_dir.exists():
                return choices
            for icon_path in sorted(self._custom_icons_dir.glob("*.png")):
                try:
                    preview = GdkPixbuf.Pixbuf.new_from_file_at_size(str(icon_path), 64, 64)
                except Exception:
                    continue
                icon_id = f"custom:{icon_path.name}"
                label = f"{icon_path.name} ({_('Custom')})"
                choices.append((icon_id, label, preview))
        except Exception:
            return choices
        preferred_token = f"{(preferred_type or '').strip().lower()}.png"
        if preferred_token:
            def _choice_sort_key(item: tuple[str, str, GdkPixbuf.Pixbuf]) -> tuple[int, str]:
                _icon_id, label, _preview = item
                label_low = label.lower()
                is_preferred = 0 if preferred_token in label_low else 1
                return (is_preferred, label_low)

            choices.sort(key=_choice_sort_key)
        return choices

    def _resolve_icon_id_to_path(self, icon_id: str) -> Path:
        if not isinstance(icon_id, str) or not icon_id:
            return self._custom_icons_dir / ""
        if ":" in icon_id:
            source, name = icon_id.split(":", 1)
            name = name.strip()
            if source == "builtin":
                return self._builtin_icons_dir / name
            if source == "custom":
                return self._custom_icons_dir / name
        # Backward compatibility with old prefs storing only filename.
        custom_candidate = self._custom_icons_dir / icon_id
        if custom_candidate.exists():
            return custom_candidate
        return self._builtin_icons_dir / icon_id

    def _load_remote_icon(self, device: Device, size: int) -> GdkPixbuf.Pixbuf | None:
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        xml_fields = metadata.get("xml_fields")
        if not isinstance(xml_fields, dict):
            return None
        icon_url = xml_fields.get("iconURL")
        if not isinstance(icon_url, str) or not icon_url.strip():
            return self._remote_icon_by_endpoint.get((device.ip, device.port))
        icon_url = icon_url.strip()
        endpoint = (device.ip, device.port)
        cached = self._remote_icon_cache.get(icon_url)
        if cached is not None:
            self._remote_icon_by_endpoint[endpoint] = cached
            return cached
        self._start_remote_icon_fetch(icon_url, endpoint, size)
        return self._remote_icon_by_endpoint.get(endpoint)

    def _start_remote_icon_fetch(self, icon_url: str, endpoint: tuple[str, int], size: int) -> None:
        if icon_url in self._remote_icon_fetching:
            return
        self._remote_icon_fetching.add(icon_url)

        def _worker() -> None:
            try:
                with urlopen(icon_url, timeout=1.5) as response:
                    data = response.read()
                loader = GdkPixbuf.PixbufLoader()
                loader.write(data)
                loader.close()
                pixbuf = loader.get_pixbuf()
                if pixbuf is not None:
                    scaled = pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
                    if scaled is not None:
                        self._remote_icon_cache[icon_url] = scaled
                        self._remote_icon_by_endpoint[endpoint] = scaled
                        from gi.repository import GLib

                        GLib.idle_add(self._refresh_icons_after_async_fetch)
            except Exception:
                pass
            finally:
                self._remote_icon_fetching.discard(icon_url)

        threading.Thread(target=_worker, daemon=True).start()

    def _refresh_icons_after_async_fetch(self) -> bool:
        self._rebuild_icon_sections(self._filtered_devices)
        return False

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"""
            .offline-device-monitored {
                opacity: 0.45;
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

    def _cell_data_func(self, column: Gtk.TreeViewColumn, cell: Gtk.CellRendererText, model, iter_, data) -> None:
        model_index = int(data)
        bundle = model.get_value(iter_, 0)
        value = model.get_value(iter_, model_index)
        cell.set_property("text", str(value) if value is not None else "")
        monitored = bool(getattr(bundle, "monitored", False))
        online = bool(getattr(bundle, "online", True))
        if (not online) and monitored:
            cell.set_property("foreground-set", True)
            cell.set_property("foreground-rgba", self._offline_fg)
        else:
            try:
                cell.set_property("foreground-set", False)
            except Exception:
                pass

    def _build_device_bundles(self, devices: list[Device]) -> list[_DeviceBundle]:
        bundles_by_endpoint: dict[tuple[str, int], _DeviceBundle] = {}
        order: list[tuple[str, int]] = []
        for device in devices:
            endpoint = (device.ip, device.port)
            bundle = bundles_by_endpoint.get(endpoint)
            if bundle is None:
                bundle = _DeviceBundle(ip=device.ip, port=device.port, primary=device)
                bundles_by_endpoint[endpoint] = bundle
                order.append(endpoint)

            if device.source == "mdns":
                bundle.mdns_device = device
                bundle.primary = device
            elif device.source == "ssdp":
                bundle.ssdp_device = device
                if bundle.mdns_device is None:
                    bundle.primary = device
            elif bundle.mdns_device is None and bundle.ssdp_device is None:
                bundle.primary = device

        return [bundles_by_endpoint[key] for key in order]
