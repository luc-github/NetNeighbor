"""Main application window."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from discovery.manager import DiscoveryManager
from ui.device_list import DeviceList
from model.device import Device
from utils.ui_prefs import load_ui_preferences, save_ui_preferences
from utils.notifications import send_notification


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application, discovery_manager: DiscoveryManager) -> None:
        super().__init__(application=application, title="NetNeighbor")
        self.set_default_size(900, 560)
        self._manager = discovery_manager
        self._device_state: dict[str, dict[str, object]] = {}
        self._notification_mode: str = "monitored"
        self._selected_category: str | None = None
        self._category_rows: dict[str, Gtk.ListBoxRow] = {}
        self._is_updating_sidebar = False
        self._initializing = True
        self._prefs = load_ui_preferences()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_border_width(8)
        self.add(root)

        menubar = Gtk.MenuBar()
        root.pack_start(menubar, False, False, 0)

        view_item = Gtk.MenuItem.new_with_label(_("View"))
        menubar.append(view_item)
        view_menu = Gtk.Menu()
        view_item.set_submenu(view_menu)

        reload_item = Gtk.ImageMenuItem.new_with_label(_("Reload"))
        reload_item.set_image(Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.MENU))
        reload_item.set_always_show_image(True)
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

        self._icons_item = Gtk.RadioMenuItem.new_with_label(None, _("Icons"))
        self._list_item = Gtk.RadioMenuItem.new_with_label_from_widget(self._icons_item, _("List"))
        self._icons_item.connect("toggled", self._on_view_toggled, "icons")
        self._list_item.connect("toggled", self._on_view_toggled, "list")
        view_menu.append(self._icons_item)
        view_menu.append(self._list_item)
        view_menu.append(Gtk.SeparatorMenuItem())

        self._source_badges_item = Gtk.CheckMenuItem.new_with_label(_("Show source badges"))
        self._source_badges_item.connect("toggled", self._on_source_badges_toggled)
        view_menu.append(self._source_badges_item)
        view_menu.append(Gtk.SeparatorMenuItem())

        preferences_item = Gtk.MenuItem.new_with_label(_("Preferences"))
        view_menu.append(preferences_item)
        preferences_menu = Gtk.Menu()
        preferences_item.set_submenu(preferences_menu)

        self._provided_icons_item = Gtk.RadioMenuItem.new_with_label(None, _("Use provided icons"))
        self._system_icons_item = Gtk.RadioMenuItem.new_with_label_from_widget(
            self._provided_icons_item, _("Use system icons")
        )
        self._provided_icons_item.connect("toggled", self._on_icon_source_toggled, "provided")
        self._system_icons_item.connect("toggled", self._on_icon_source_toggled, "system")
        preferences_menu.append(self._provided_icons_item)
        preferences_menu.append(self._system_icons_item)

        preferences_menu.append(Gtk.SeparatorMenuItem())

        notif_off_item = Gtk.RadioMenuItem.new_with_label(None, _("Notifications off"))
        notif_monitored_item = Gtk.RadioMenuItem.new_with_label_from_widget(notif_off_item, _("Monitored devices only"))
        notif_all_item = Gtk.RadioMenuItem.new_with_label_from_widget(notif_off_item, _("All devices"))

        self._notif_off_item = notif_off_item
        self._notif_monitored_item = notif_monitored_item
        self._notif_all_item = notif_all_item

        notif_off_item.connect("toggled", self._on_notification_mode_toggled, "off")
        notif_monitored_item.connect("toggled", self._on_notification_mode_toggled, "monitored")
        notif_all_item.connect("toggled", self._on_notification_mode_toggled, "all")

        preferences_menu.append(notif_off_item)
        preferences_menu.append(notif_monitored_item)
        preferences_menu.append(notif_all_item)

        help_item = Gtk.MenuItem.new_with_label(_("Help"))
        menubar.append(help_item)
        help_menu = Gtk.Menu()
        help_item.set_submenu(help_menu)

        about_item = Gtk.MenuItem.new_with_label(_("About"))
        about_item.connect("activate", self._on_about_activate)
        help_menu.append(about_item)

        self._content = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        self._content.set_position(220)
        self._content.connect("notify::position", self._on_sidebar_position_changed)
        root.pack_start(self._content, True, True, 0)

        self._sidebar_list = Gtk.ListBox()
        self._sidebar_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._sidebar_list.connect("row-selected", self._on_sidebar_row_selected)
        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.add(self._sidebar_list)
        self._content.add1(sidebar_scroll)

        self._device_list = DeviceList(parent_window=self, on_set_monitored=self._on_set_monitored)
        # DeviceList will call this when user chooses Monitor/Unfollow.
        self._content.add2(self._device_list)

        self._apply_ui_preferences()

        self._manager.add_listener(self._on_devices_updated)
        self._manager.start()

        self._initializing = False
        self.connect("destroy", self._on_destroy)
        self.show_all()

    def _on_reload_activate(self, _menu_item: Gtk.MenuItem) -> None:
        self._manager.refresh()

    def _on_view_toggled(self, menu_item: Gtk.RadioMenuItem, view_mode: str) -> None:
        if menu_item.get_active():
            self._device_list.set_view_mode(view_mode)
            self._persist_ui_preferences()

    def _on_source_badges_toggled(self, menu_item: Gtk.CheckMenuItem) -> None:
        self._device_list.set_show_source_badges(menu_item.get_active())
        self._persist_ui_preferences()

    def _on_icon_source_toggled(self, menu_item: Gtk.RadioMenuItem, mode: str) -> None:
        if menu_item.get_active():
            self._device_list.set_icon_source_mode(mode)
            self._persist_ui_preferences()

    def _on_notification_mode_toggled(self, menu_item: Gtk.RadioMenuItem, mode: str) -> None:
        if not menu_item.get_active():
            return
        self._notification_mode = mode
        self._persist_ui_preferences()

    def _on_set_monitored(self, device: Device, monitored: bool) -> None:
        # For immediate feedback: if user starts monitoring a device that is already offline,
        # show a "left" notification when notifications are in "monitored devices only" mode.
        if monitored and getattr(self, "_notification_mode", "off") == "monitored":
            self._device_state[device.key] = {
                "online": bool(device.online),
                "monitored": True,
                "name": device.name,
            }
            if not device.online:
                send_notification("NetNeighbor", device.name, f"{device.name} left")
        self._manager.set_device_monitored(device.key, monitored)

    def _on_about_activate(self, _menu_item: Gtk.MenuItem) -> None:
        dialog = Gtk.AboutDialog(transient_for=self, modal=True)
        dialog.set_program_name("NetNeighbor")
        dialog.set_version("0.1.0-dev")
        dialog.set_authors(["Luc"])
        dialog.set_comments(_("Linux network neighborhood for SSDP/mDNS discovery."))
        dialog.set_website("https://github.com/luc-github/NetNeighbor")
        dialog.set_website_label(_("GitHub Project"))
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
        self._notify_device_transitions(devices)
        self._rebuild_sidebar(devices)
        self._device_list.set_devices(devices)
        return False

    def _notify_device_transitions(self, devices: list[Device]) -> None:
        mode = getattr(self, "_notification_mode", "off")
        if mode == "off":
            # Still update cache below.
            self._sync_device_state(devices)
            return

        current_keys = {d.key for d in devices}

        # 1) Disappeared devices => left (depending on notification mode).
        for key, prev in list(self._device_state.items()):
            if key in current_keys:
                continue

            prev_online = bool(prev.get("online"))
            prev_monitored = bool(prev.get("monitored"))
            if mode == "all" and prev_online:
                name = str(prev.get("name", "Device"))
                send_notification("NetNeighbor", name, f"{name} left")
            elif mode == "monitored" and prev_online and prev_monitored:
                name = str(prev.get("name", "Device"))
                send_notification("NetNeighbor", name, f"{name} left")
            del self._device_state[key]

        # 2) Online/offline transitions => connected/left.
        for device in devices:
            prev = self._device_state.get(device.key)
            if prev is None:
                # First sighting.
                if bool(device.online) and (mode == "all" or bool(getattr(device, "monitored", False))):
                    name = device.name
                    send_notification("NetNeighbor", name, f"{name} connected")
                self._device_state[device.key] = {
                    "online": bool(device.online),
                    "monitored": bool(getattr(device, "monitored", False)),
                    "name": device.name,
                }
                continue

            prev_online = bool(prev.get("online"))
            self._device_state[device.key] = {
                "online": bool(device.online),
                "monitored": bool(getattr(device, "monitored", False)),
                "name": device.name,
            }

            # Only notify for monitored devices in "monitored devices only" mode.
            if mode == "monitored" and not bool(getattr(device, "monitored", False)):
                continue

            if prev_online is False and device.online is True:
                send_notification("NetNeighbor", device.name, f"{device.name} connected")
            elif prev_online is True and device.online is False:
                send_notification("NetNeighbor", device.name, f"{device.name} left")

    def _sync_device_state(self, devices: list[Device]) -> None:
        current_keys = {d.key for d in devices}
        # Remove keys that disappeared.
        for key in list(self._device_state.keys()):
            if key not in current_keys:
                del self._device_state[key]
        for device in devices:
            self._device_state[device.key] = {
                "online": bool(device.online),
                "monitored": bool(getattr(device, "monitored", False)),
                "name": device.name,
            }

    def _rebuild_sidebar(self, devices) -> None:
        counts: dict[str, int] = {}
        for device in devices:
            counts[device.category] = counts.get(device.category, 0) + 1

        selected = self._selected_category
        self._is_updating_sidebar = True
        for child in self._sidebar_list.get_children():
            self._sidebar_list.remove(child)
        self._category_rows.clear()

        all_label = f"{_('All')} ({len(devices)})"
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
        self._persist_ui_preferences()

    def _on_sidebar_position_changed(self, _paned: Gtk.Paned, _param) -> None:
        self._persist_ui_preferences()

    def _apply_ui_preferences(self) -> None:
        view_mode = self._prefs.get("view_mode", "icons")
        show_source_badges = bool(self._prefs.get("show_source_badges", True))
        sidebar_position = int(self._prefs.get("sidebar_position", 220))
        icon_source_mode = self._prefs.get("icon_source_mode", "provided")
        # Backward compat: older versions saved a boolean.
        if isinstance(self._prefs.get("use_notifications"), bool):
            self._notification_mode = "monitored" if self._prefs.get("use_notifications") else "off"
        else:
            self._notification_mode = str(self._prefs.get("notification_mode", "off"))
        selected_category = self._prefs.get("selected_category")
        if isinstance(selected_category, str):
            self._selected_category = selected_category
        else:
            self._selected_category = None

        self._content.set_position(max(160, min(sidebar_position, 480)))
        self._source_badges_item.set_active(show_source_badges)
        if icon_source_mode == "system":
            self._system_icons_item.set_active(True)
        else:
            self._provided_icons_item.set_active(True)
        if view_mode == "list":
            self._list_item.set_active(True)
        else:
            self._icons_item.set_active(True)

        # Restore selected radio option.
        if hasattr(self, "_notif_off_item") and hasattr(self, "_notif_monitored_item") and hasattr(self, "_notif_all_item"):
            self._notif_off_item.set_active(self._notification_mode == "off")
            self._notif_monitored_item.set_active(self._notification_mode == "monitored")
            self._notif_all_item.set_active(self._notification_mode == "all")

    def _persist_ui_preferences(self) -> None:
        if self._initializing:
            return
        prefs = {
            "view_mode": self._device_list.view_mode,
            "show_source_badges": self._source_badges_item.get_active(),
            "icon_source_mode": self._device_list.icon_source_mode,
            "notification_mode": self._notification_mode,
            "selected_category": self._selected_category,
            "sidebar_position": self._content.get_position(),
        }
        save_ui_preferences(prefs)

    def _on_destroy(self, *_args) -> None:
        self._persist_ui_preferences()
        self._manager.stop()
