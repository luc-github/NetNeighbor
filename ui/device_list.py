"""Device view supporting list and icon modes."""

import gi
from collections import defaultdict
from dataclasses import dataclass

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
        badges: list[tuple[str, Gtk.Align]] = []
        if self.mdns_device is not None:
            badges.append(("mDNS", Gtk.Align.START))
        if self.ssdp_device is not None:
            badges.append(("SSDP", Gtk.Align.END))
        if badges:
            return badges
        return [(self.primary.source.upper(), Gtk.Align.END)]

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
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._current_view = "icons"
        self._devices: list[Device] = []
        self._filtered_devices: list[_DeviceBundle] = []
        self._category_filter: str | None = None
        self._parent_window = parent_window
        self._on_set_monitored = on_set_monitored
        self._show_source_badges = True
        self._icon_source_mode = "provided"
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

    def set_show_source_badges(self, enabled: bool) -> None:
        self._show_source_badges = enabled
        self._rebuild_icon_sections(self._filtered_devices)

    def set_icon_source_mode(self, mode: str) -> None:
        if mode not in {"provided", "system"}:
            return
        self._icon_source_mode = mode
        self._rebuild_icon_sections(self._filtered_devices)

    @property
    def icon_source_mode(self) -> str:
        return self._icon_source_mode

    @property
    def view_mode(self) -> str:
        return self._current_view

    def _rebuild_icon_sections(self, bundles: list[_DeviceBundle]) -> None:
        for child in self._icon_sections.get_children():
            self._icon_sections.remove(child)

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

        if self._show_source_badges:
            for label, align in bundle.badges:
                badge = Gtk.Label(label=label)
                badge.get_style_context().add_class("source-badge")
                badge.set_halign(align)
                badge.set_valign(Gtk.Align.END)
                if align == Gtk.Align.START:
                    badge.set_margin_start(0)
                else:
                    badge.set_margin_end(0)
                badge.set_margin_bottom(0)
                icon_overlay.add_overlay(badge)

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

        if bundle.monitored:
            unfollow_item = Gtk.MenuItem.new_with_label(_("Unfollow"))
            unfollow_item.connect("activate", self._on_monitor_item_activate, bundle, False)
            menu.append(unfollow_item)
        else:
            follow_item = Gtk.MenuItem.new_with_label(_("Monitor"))
            follow_item.connect("activate", self._on_monitor_item_activate, bundle, True)
            menu.append(follow_item)

        menu.show_all()
        menu.popup_at_pointer(event)

    def _on_monitor_item_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle, monitored: bool) -> None:
        if self._on_set_monitored is None:
            return
        for device in bundle.devices:
            self._on_set_monitored(device, monitored)

    def _on_open_item_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle) -> None:
        self._open_device(bundle)

    def _on_show_ssdp_xml_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle) -> None:
        device = bundle.ssdp_device
        if device is None:
            return
        fields, raw_xml, services_list, troubleshooting_fields, xml_location = build_ssdp_payload(device)
        self._show_details_dialog(
            title=f"{_('SSDP details')} - {bundle.name}",
            fields=fields,
            raw_content=raw_xml,
            raw_button_label=_("Show raw XML"),
            services_list=services_list if services_list else None,
            troubleshooting_records=troubleshooting_fields,
            raw_xml_location=xml_location,
        )

    def _on_show_mdns_txt_activate(self, _item: Gtk.MenuItem, bundle: _DeviceBundle) -> None:
        device = bundle.mdns_device
        if device is None:
            return
        fields, txt_records, services_list = build_mdns_payload(device)
        self._show_details_dialog(
            title=f"{_('mDNS details')} - {bundle.name}",
            fields=fields,
            txt_records=txt_records,
            services_list=services_list,
        )

    def _open_device(self, bundle: _DeviceBundle) -> None:
        url = bundle.open_url
        if not url:
            return
        open_url(url)

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
        )
        dialog.run()
        dialog.destroy()

    def _has_ssdp_details(self, bundle: _DeviceBundle) -> bool:
        return bundle.ssdp_device is not None

    def _has_mdns_details(self, bundle: _DeviceBundle) -> bool:
        return bundle.mdns_device is not None

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
        if self._icon_source_mode == "provided":
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

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"""
            .source-badge {
                background-color: rgba(0, 0, 0, 0.85);
                color: #ffffff;
                border-radius: 8px;
                padding: 1px 6px;
                font-size: 10px;
                font-weight: 700;
            }
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
