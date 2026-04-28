"""Device view supporting list and icon modes."""

import gi
from collections import defaultdict

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk

from model.device import Device
from ui.device_details import DeviceDetailsDialog
from ui.icons import resolve_icon_path
from utils.browser import open_url


class DeviceList(Gtk.Box):
    def __init__(self, parent_window: Gtk.Window | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._current_view = "icons"
        self._devices: list[Device] = []
        self._filtered_devices: list[Device] = []
        self._category_filter: str | None = None
        self._parent_window = parent_window

        self._list_store = Gtk.ListStore(object, str, str, int, str, str)
        self._tree = Gtk.TreeView(model=self._list_store)
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
        self._filtered_devices = self._apply_category_filter(devices)
        self._list_store.clear()
        for device in self._filtered_devices:
            self._list_store.append([device, device.name, device.ip, device.port, device.category, device.source])
        self._rebuild_icon_sections(self._filtered_devices)

    def set_category_filter(self, category: str | None) -> None:
        self._category_filter = category
        self.set_devices(self._devices)

    def set_view_mode(self, view_mode: str) -> None:
        if view_mode not in {"list", "icons"}:
            return
        self._current_view = view_mode
        self._stack.set_visible_child_name(view_mode)

    @property
    def view_mode(self) -> str:
        return self._current_view

    def _rebuild_icon_sections(self, devices: list[Device]) -> None:
        for child in self._icon_sections.get_children():
            self._icon_sections.remove(child)

        grouped_devices: dict[str, list[Device]] = defaultdict(list)
        for device in devices:
            grouped_devices[device.category].append(device)

        for category in sorted(grouped_devices.keys()):
            frame = Gtk.Frame(label=category)
            frame.set_shadow_type(Gtk.ShadowType.IN)
            frame_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            frame_content.set_border_width(8)
            frame.add(frame_content)

            flow = Gtk.FlowBox()
            flow.set_max_children_per_line(6)
            flow.set_selection_mode(Gtk.SelectionMode.NONE)
            flow.set_row_spacing(10)
            flow.set_column_spacing(10)
            flow.set_homogeneous(False)

            for device in sorted(grouped_devices[category], key=lambda item: item.name.lower()):
                flow.add(self._build_icon_tile(device))

            frame_content.pack_start(flow, False, False, 0)
            self._icon_sections.pack_start(frame, False, False, 0)

        self._icon_sections.show_all()

    def _build_icon_tile(self, device: Device) -> Gtk.Widget:
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tile.set_size_request(130, -1)

        image = Gtk.Image.new_from_pixbuf(self._load_device_icon(device))
        image.set_halign(Gtk.Align.CENTER)
        tile.pack_start(image, False, False, 0)

        name_label = Gtk.Label(label=device.name)
        name_label.set_line_wrap(True)
        name_label.set_max_width_chars(18)
        name_label.set_justify(Gtk.Justification.CENTER)
        name_label.set_halign(Gtk.Align.CENTER)
        tile.pack_start(name_label, False, False, 0)

        ip_label = Gtk.Label(label=device.ip)
        ip_label.get_style_context().add_class("dim-label")
        ip_label.set_halign(Gtk.Align.CENTER)
        tile.pack_start(ip_label, False, False, 0)

        container = Gtk.EventBox()
        container.add(tile)
        container.set_visible_window(False)
        container.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        container.connect("button-press-event", self._on_icon_tile_button_press, device)
        return container

    def _on_icon_tile_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton, device: Device) -> bool:
        if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS and event.button == 1:
            self._open_device(device)
            return True
        if event.button == 3:
            self._show_device_context_menu(device, event)
            return True
        return False

    def _show_device_context_menu(self, device: Device, event: Gdk.EventButton) -> None:
        menu = Gtk.Menu()

        open_item = Gtk.MenuItem.new_with_label("Open")
        open_item.connect("activate", self._on_open_item_activate, device)
        open_item.set_sensitive(bool(device.url))
        menu.append(open_item)

        ssdp_item = Gtk.MenuItem.new_with_label("Show SSDP XML")
        ssdp_item.connect("activate", self._on_show_ssdp_xml_activate, device)
        ssdp_item.set_sensitive(self._has_ssdp_details(device))
        menu.append(ssdp_item)

        mdns_item = Gtk.MenuItem.new_with_label("Show mDNS TXT records")
        mdns_item.connect("activate", self._on_show_mdns_txt_activate, device)
        mdns_item.set_sensitive(self._has_mdns_details(device))
        menu.append(mdns_item)

        menu.show_all()
        menu.popup_at_pointer(event)

    def _on_open_item_activate(self, _item: Gtk.MenuItem, device: Device) -> None:
        self._open_device(device)

    def _on_show_ssdp_xml_activate(self, _item: Gtk.MenuItem, device: Device) -> None:
        content = self._build_ssdp_details(device)
        self._show_details_dialog(f"SSDP XML - {device.name}", content)

    def _on_show_mdns_txt_activate(self, _item: Gtk.MenuItem, device: Device) -> None:
        content = self._build_mdns_details(device)
        self._show_details_dialog(f"mDNS TXT - {device.name}", content)

    def _open_device(self, device: Device) -> None:
        if not device.url:
            return
        open_url(device.url)

    def _show_details_dialog(self, title: str, content: str) -> None:
        parent = self._parent_window
        if parent is None:
            top_level = self.get_toplevel()
            if isinstance(top_level, Gtk.Window):
                parent = top_level
        if parent is None:
            return
        dialog = DeviceDetailsDialog(parent=parent, title=title, content=content)
        dialog.run()
        dialog.destroy()

    def _has_ssdp_details(self, device: Device) -> bool:
        if device.source == "ssdp":
            return True
        return any(key in device.metadata for key in {"xml", "location", "usn", "st", "deviceType"})

    def _has_mdns_details(self, device: Device) -> bool:
        if device.source == "mdns":
            return True
        return any(key in device.metadata for key in {"txt", "service", "hostname", "server"})

    def _build_ssdp_details(self, device: Device) -> str:
        xml_data = device.metadata.get("xml")
        if isinstance(xml_data, str) and xml_data.strip():
            return xml_data
        return self._format_metadata(device.metadata)

    def _build_mdns_details(self, device: Device) -> str:
        txt_data = device.metadata.get("txt")
        if isinstance(txt_data, dict):
            return "\n".join(f"{key}={value}" for key, value in txt_data.items())
        if isinstance(txt_data, list):
            return "\n".join(str(item) for item in txt_data)
        if isinstance(txt_data, str) and txt_data.strip():
            return txt_data
        return self._format_metadata(device.metadata)

    def _format_metadata(self, metadata: dict) -> str:
        if not metadata:
            return "No details available."
        return "\n".join(f"{key}: {value}" for key, value in sorted(metadata.items(), key=lambda item: item[0]))

    def _apply_category_filter(self, devices: list[Device]) -> list[Device]:
        if not self._category_filter:
            return list(devices)
        return [device for device in devices if device.category == self._category_filter]

    def _on_tree_row_activated(self, _tree: Gtk.TreeView, path: Gtk.TreePath, _column: Gtk.TreeViewColumn) -> None:
        model = self._tree.get_model()
        tree_iter = model.get_iter(path)
        device = model.get_value(tree_iter, 0)
        if isinstance(device, Device):
            self._open_device(device)

    def _on_tree_button_press(self, tree: Gtk.TreeView, event: Gdk.EventButton) -> bool:
        if event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS:
            hit = tree.get_path_at_pos(int(event.x), int(event.y))
            if hit is None:
                return False
            path, _column, _x, _y = hit
            model = tree.get_model()
            tree_iter = model.get_iter(path)
            device = model.get_value(tree_iter, 0)
            if isinstance(device, Device):
                self._open_device(device)
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
            device = model.get_value(tree_iter, 0)
            if isinstance(device, Device):
                self._show_device_context_menu(device, event)
                return True
        return False

    def _load_device_icon(self, device: Device) -> GdkPixbuf.Pixbuf:
        icon_path = resolve_icon_path(device.icon)
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_size(str(icon_path), 64, 64)
        except Exception:
            theme = Gtk.IconTheme.get_default()
            fallback_names = ["network-workgroup", "network-server", "computer", "folder"]
            for icon_name in fallback_names:
                try:
                    return theme.load_icon(icon_name, 64, 0)
                except Exception:
                    continue
            return GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 64, 64)

    def _add_column(self, title: str, model_index: int) -> None:
        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn(title, renderer, text=model_index)
        column.set_resizable(True)
        self._tree.append_column(column)
