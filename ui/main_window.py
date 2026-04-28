"""Main application window."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from discovery.manager import DiscoveryManager
from ui.device_list import DeviceList


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application, discovery_manager: DiscoveryManager) -> None:
        super().__init__(application=application, title="NetNeighbor")
        self.set_default_size(900, 560)
        self._manager = discovery_manager
        self._selected_category: str | None = None
        self._category_rows: dict[str, Gtk.ListBoxRow] = {}
        self._is_updating_sidebar = False

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_border_width(8)
        self.add(root)

        menubar = Gtk.MenuBar()
        root.pack_start(menubar, False, False, 0)

        view_item = Gtk.MenuItem.new_with_label("View")
        menubar.append(view_item)
        view_menu = Gtk.Menu()
        view_item.set_submenu(view_menu)

        reload_item = Gtk.MenuItem.new_with_label("Reload")
        reload_item.connect("activate", self._on_reload_activate)
        view_menu.append(reload_item)
        reload_item.add_accelerator(
            "activate",
            self._build_accelerators(),
            Gdk.KEY_r,
            Gdk.ModifierType.CONTROL_MASK,
            Gtk.AccelFlags.VISIBLE,
        )

        view_menu.append(Gtk.SeparatorMenuItem())

        self._icons_item = Gtk.RadioMenuItem.new_with_label(None, "Icons")
        self._list_item = Gtk.RadioMenuItem.new_with_label_from_widget(self._icons_item, "List")
        self._icons_item.connect("toggled", self._on_view_toggled, "icons")
        self._list_item.connect("toggled", self._on_view_toggled, "list")
        view_menu.append(self._icons_item)
        view_menu.append(self._list_item)
        self._icons_item.set_active(True)

        about_item = Gtk.MenuItem.new_with_label("About")
        about_item.set_right_justified(True)
        about_item.connect("activate", self._on_about_activate)
        menubar.append(about_item)

        content = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        content.set_position(220)
        root.pack_start(content, True, True, 0)

        self._sidebar_list = Gtk.ListBox()
        self._sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._sidebar_list.connect("row-selected", self._on_sidebar_row_selected)
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.add(self._sidebar_list)
        content.add1(sidebar_scroll)

        self._device_list = DeviceList(parent_window=self)
        content.add2(self._device_list)

        self._manager.add_listener(self._on_devices_updated)
        self._manager.start()

        self.connect("destroy", self._on_destroy)
        self.show_all()

    def _on_reload_activate(self, _menu_item: Gtk.MenuItem) -> None:
        self._manager.refresh()

    def _on_view_toggled(self, menu_item: Gtk.RadioMenuItem, view_mode: str) -> None:
        if menu_item.get_active():
            self._device_list.set_view_mode(view_mode)

    def _on_about_activate(self, _menu_item: Gtk.MenuItem) -> None:
        dialog = Gtk.AboutDialog(transient_for=self, modal=True)
        dialog.set_program_name("NetNeighbor")
        dialog.set_version("0.1.0-dev")
        dialog.set_authors(["Luc"])
        dialog.set_comments("Linux network neighborhood for SSDP/mDNS discovery.")
        dialog.set_website("https://github.com/luc-github/NetNeighbor")
        dialog.set_website_label("GitHub Project")
        dialog.set_license_type(Gtk.License.LGPL_3_0)
        dialog.set_copyright("Copyright (C) Luc")
        dialog.run()
        dialog.destroy()

    def _build_accelerators(self) -> Gtk.AccelGroup:
        accel_group = Gtk.AccelGroup()
        self.add_accel_group(accel_group)
        return accel_group

    def _on_devices_updated(self, devices) -> None:
        GLib.idle_add(self._update_ui_devices, devices)

    def _update_ui_devices(self, devices) -> bool:
        self._rebuild_sidebar(devices)
        self._device_list.set_devices(devices)
        return False

    def _rebuild_sidebar(self, devices) -> None:
        counts: dict[str, int] = {}
        for device in devices:
            counts[device.category] = counts.get(device.category, 0) + 1

        selected = self._selected_category
        self._is_updating_sidebar = True
        for child in self._sidebar_list.get_children():
            self._sidebar_list.remove(child)
        self._category_rows.clear()

        all_label = f"All ({len(devices)})"
        self._add_sidebar_row(all_label, None)
        for category in sorted(counts.keys()):
            self._add_sidebar_row(f"{category} ({counts[category]})", category)

        target_row = self._category_rows.get(selected) if selected in self._category_rows else self._category_rows.get(None)
        if target_row is not None:
            self._sidebar_list.select_row(target_row)
        self._is_updating_sidebar = False

    def _add_sidebar_row(self, text: str, category: str | None) -> None:
        row = Gtk.ListBoxRow()
        row.category = category
        label = Gtk.Label(label=text, xalign=0.0)
        label.set_margin_start(10)
        label.set_margin_end(10)
        label.set_margin_top(6)
        label.set_margin_bottom(6)
        row.add(label)
        self._sidebar_list.add(row)
        self._category_rows[category] = row
        row.show_all()

    def _on_sidebar_row_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._is_updating_sidebar or row is None:
            return
        self._selected_category = getattr(row, "category", None)
        self._device_list.set_category_filter(self._selected_category)

    def _on_destroy(self, *_args) -> None:
        self._manager.stop()
