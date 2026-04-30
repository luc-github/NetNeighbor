"""Main application window."""

import gi
import logging
from datetime import datetime
import threading

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from discovery.manager import DiscoveryManager
from ui.device_list import DeviceList
from model.device import Device
from utils.ui_prefs import load_ui_preferences, save_ui_preferences
from utils.notifications import send_notification

_LOG = logging.getLogger(__name__)
_DEFAULT_LOCATION_OPTIONS = ["Office", "Room", "Living room", "Kitchen", "Workshop", "Garage"]


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
        self._sidebar_signature: tuple | None = None
        self._initializing = True
        self._prefs = load_ui_preferences()
        self._notification_history: list[dict[str, str]] = []
        self._location_options: list[str] = []

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

        view_title_item = Gtk.MenuItem.new_with_label(_("Display:"))
        view_title_item.set_sensitive(False)
        view_menu.append(view_title_item)

        self._icons_item = Gtk.RadioMenuItem.new_with_label(None, _("Icons"))
        self._list_item = Gtk.RadioMenuItem.new_with_label_from_widget(self._icons_item, _("List"))
        self._icons_item.connect("toggled", self._on_view_toggled, "icons")
        self._list_item.connect("toggled", self._on_view_toggled, "list")
        view_menu.append(self._icons_item)
        view_menu.append(self._list_item)
        view_menu.append(Gtk.SeparatorMenuItem())

        arrange_title_item = Gtk.MenuItem.new_with_label(_("Arrange:"))
        arrange_title_item.set_sensitive(False)
        view_menu.append(arrange_title_item)
        self._icons_unsorted_item = Gtk.RadioMenuItem.new_with_label(None, _("Unsorted"))
        self._icons_sorted_item = Gtk.RadioMenuItem.new_with_label_from_widget(
            self._icons_unsorted_item, _("by Type")
        )
        self._icons_by_location_item = Gtk.RadioMenuItem.new_with_label_from_widget(
            self._icons_unsorted_item, _("by Location")
        )
        self._icons_unsorted_item.connect("toggled", self._on_icon_sort_mode_toggled, "appearance")
        self._icons_sorted_item.connect("toggled", self._on_icon_sort_mode_toggled, "sorted")
        self._icons_by_location_item.connect("toggled", self._on_icon_sort_mode_toggled, "location")
        view_menu.append(self._icons_unsorted_item)
        view_menu.append(self._icons_sorted_item)
        view_menu.append(self._icons_by_location_item)
        view_menu.append(Gtk.SeparatorMenuItem())

        preferences_item = Gtk.MenuItem.new_with_label(_("Preferences"))
        view_menu.append(preferences_item)
        preferences_menu = Gtk.Menu()
        preferences_item.set_submenu(preferences_menu)

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
        preferences_menu.append(Gtk.SeparatorMenuItem())
        locations_item = Gtk.MenuItem.new_with_label(_("Location presets"))
        locations_item.connect("activate", self._on_locations_presets_activate)
        preferences_menu.append(locations_item)

        self._notifications_item = Gtk.MenuItem.new_with_label(_("Tools"))
        menubar.append(self._notifications_item)
        notifications_menu = Gtk.Menu()
        self._notifications_item.set_submenu(notifications_menu)
        notifications_history_item = Gtk.MenuItem.new_with_label(_("Notifications history"))
        notifications_history_item.connect("activate", self._on_notifications_history_activate)
        notifications_menu.append(notifications_history_item)

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

        self._device_list = DeviceList(
            parent_window=self,
            on_set_monitored=self._on_set_monitored,
            on_icon_mode_changed=self._on_icon_mode_changed,
            on_set_type_override=self._on_set_type_override,
            on_set_name_override=self._on_set_name_override,
            on_set_location_override=self._on_set_location_override,
        )
        # DeviceList will call this when user chooses Monitor/Unfollow.
        self._content.add2(self._device_list)

        self._apply_ui_preferences()

        self._manager.add_listener(self._on_devices_updated)
        GLib.idle_add(self._start_discovery_protocols)

        self._initializing = False
        self.connect("destroy", self._on_destroy)
        self.show_all()

    def _start_discovery_protocols(self) -> bool:
        self._manager.start()
        return False

    def _on_reload_activate(self, _menu_item: Gtk.MenuItem) -> None:
        self._manager.refresh()

    def _on_view_toggled(self, menu_item: Gtk.RadioMenuItem, view_mode: str) -> None:
        if menu_item.get_active():
            self._device_list.set_view_mode(view_mode)
            self._refresh_icon_sort_menu_state()
            self._rebuild_sidebar(self._manager.devices)
            self._persist_ui_preferences()

    def _on_icon_sort_mode_toggled(self, menu_item: Gtk.RadioMenuItem, mode: str) -> None:
        if not menu_item.get_active():
            return
        self._device_list.set_icon_sort_mode(mode)
        self._rebuild_sidebar(self._manager.devices)
        self._persist_ui_preferences()

    def _on_icon_mode_changed(self) -> None:
        self._persist_ui_preferences()

    def _on_notification_mode_toggled(self, menu_item: Gtk.RadioMenuItem, mode: str) -> None:
        if not menu_item.get_active():
            return
        self._notification_mode = mode
        self._refresh_notifications_menu_state()
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
                self._emit_device_notification(device.name, "left")
        self._manager.set_device_monitored(device.key, monitored)

    def _on_set_type_override(self, source: str, ip: str, port: int, device_type: str | None) -> None:
        self._manager.set_device_type_override(source, ip, port, device_type)
        self._persist_ui_preferences()

    def _on_set_name_override(self, source: str, ip: str, port: int, device_name: str | None) -> None:
        self._manager.set_device_name_override(source, ip, port, device_name)
        self._persist_ui_preferences()

    def _on_set_location_override(self, source: str, ip: str, port: int, location: str | None) -> None:
        self._manager.set_device_location_override(source, ip, port, location)
        self._persist_ui_preferences()

    def _on_locations_presets_activate(self, _menu_item: Gtk.MenuItem) -> None:
        dialog = Gtk.Dialog(title=_("Location presets"), transient_for=self, modal=True)
        dialog.set_default_size(460, 340)
        content = dialog.get_content_area()
        content.set_border_width(8)
        label = Gtk.Label(
            label=_("Manage location presets."),
            xalign=0.0,
        )
        label.set_line_wrap(True)
        content.pack_start(label, False, False, 4)

        presets_store = Gtk.ListStore(str)
        for value in self._location_options:
            presets_store.append([value])
        presets_view = Gtk.TreeView(model=presets_store)
        presets_view.set_headers_visible(False)
        presets_view.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        presets_view.append_column(Gtk.TreeViewColumn(_("Preset"), Gtk.CellRendererText(), text=0))
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.add(presets_view)
        content.pack_start(scroll, True, True, 4)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        add_button = Gtk.Button.new_with_label(_("Add"))
        rename_button = Gtk.Button.new_with_label(_("Rename"))
        remove_button = Gtk.Button.new_with_label(_("Remove"))
        clear_button = Gtk.Button.new_with_label(_("Clear all"))
        controls.pack_start(add_button, False, False, 0)
        controls.pack_start(rename_button, False, False, 0)
        controls.pack_start(remove_button, False, False, 0)
        controls.pack_start(clear_button, False, False, 0)
        content.pack_start(controls, False, False, 2)

        rename_map: dict[str, str] = {}

        def _prompt_text(title: str, initial: str = "") -> str | None:
            prompt = Gtk.Dialog(title=title, transient_for=dialog, modal=True)
            prompt.set_default_size(320, -1)
            area = prompt.get_content_area()
            area.set_border_width(8)
            entry = Gtk.Entry()
            entry.set_text(initial)
            entry.select_region(0, -1)
            area.pack_start(entry, False, False, 0)
            prompt.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
            prompt.add_button(_("OK"), Gtk.ResponseType.OK)
            prompt.show_all()
            response_prompt = prompt.run()
            value = None
            if response_prompt == Gtk.ResponseType.OK:
                text = entry.get_text().strip()
                value = text if text else None
            prompt.destroy()
            return value

        def _selected_iter():
            _model, tree_iter = presets_view.get_selection().get_selected()
            return tree_iter

        def _existing_values() -> list[str]:
            values: list[str] = []
            tree_iter = presets_store.get_iter_first()
            while tree_iter is not None:
                value = presets_store.get_value(tree_iter, 0)
                if isinstance(value, str) and value.strip():
                    values.append(value.strip())
                tree_iter = presets_store.iter_next(tree_iter)
            return values

        def _on_add_clicked(_button: Gtk.Button) -> None:
            new_value = _prompt_text(_("Add location preset"))
            if not new_value:
                return
            if new_value in _existing_values():
                return
            presets_store.append([new_value])

        def _on_rename_clicked(_button: Gtk.Button) -> None:
            tree_iter = _selected_iter()
            if tree_iter is None:
                return
            old_value = presets_store.get_value(tree_iter, 0)
            if not isinstance(old_value, str):
                return
            new_value = _prompt_text(_("Rename location preset"), old_value)
            if not new_value or new_value == old_value:
                return
            if new_value in _existing_values():
                return
            presets_store.set_value(tree_iter, 0, new_value)
            rename_map[old_value] = new_value

        def _on_remove_clicked(_button: Gtk.Button) -> None:
            tree_iter = _selected_iter()
            if tree_iter is None:
                return
            presets_store.remove(tree_iter)

        def _on_clear_clicked(_button: Gtk.Button) -> None:
            presets_store.clear()

        add_button.connect("clicked", _on_add_clicked)
        rename_button.connect("clicked", _on_rename_clicked)
        remove_button.connect("clicked", _on_remove_clicked)
        clear_button.connect("clicked", _on_clear_clicked)

        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Save"), Gtk.ResponseType.OK)
        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            previous_options = list(self._location_options)
            self._location_options = _existing_values()
            self._device_list.set_location_options(self._location_options)
            self._apply_location_preset_changes(previous_options, self._location_options, rename_map)
            self._persist_ui_preferences()
        dialog.destroy()

    def _apply_location_preset_changes(
        self, old_options: list[str], new_options: list[str], rename_map: dict[str, str]
    ) -> None:
        overrides = self._manager.get_location_overrides()
        if not overrides:
            return
        removed_values = {value for value in old_options if value not in new_options}
        changed = False
        for key, value in list(overrides.items()):
            if value in rename_map:
                overrides[key] = rename_map[value]
                changed = True
                continue
            if value in removed_values:
                overrides.pop(key, None)
                changed = True
        if changed:
            self._manager.set_location_overrides(overrides)

    def _on_about_activate(self, _menu_item: Gtk.MenuItem) -> None:
        _LOG.debug("About menu clicked")
        dialog = Gtk.AboutDialog(transient_for=self, modal=True)
        dialog.set_default_response(Gtk.ResponseType.CLOSE)
        close_button = dialog.get_widget_for_response(Gtk.ResponseType.CLOSE)
        if close_button is not None:
            close_button.connect("clicked", self._on_about_close_clicked)
            close_button.set_receives_default(True)
            close_button.grab_default()
            close_button.grab_focus()
            dialog.set_focus(close_button)
        dialog.set_program_name("NetNeighbor")
        dialog.set_version("0.1.0-dev")
        dialog.set_authors(["Luc"])
        dialog.set_comments(_("Linux network neighborhood for SSDP/mDNS discovery."))
        dialog.add_credit_section(
            _("Python libraries"),
            [
                "PyGObject (GTK 3)",
                "zeroconf",
                "requests",
            ],
        )
        dialog.add_credit_section(
            _("Icons"),
            [
                "Custom app icons: Luc LEBOSSE",
                "System icons: active GTK icon theme",
            ],
        )
        dialog.set_website("https://github.com/luc-github/NetNeighbor")
        dialog.set_website_label(_("GitHub Project"))
        dialog.set_license_type(Gtk.License.LGPL_3_0)
        dialog.set_copyright("Copyright (C) Luc LEBOSSE")
        dialog.connect("response", self._on_about_response)
        dialog.run()
        dialog.destroy()

    def _on_about_close_clicked(self, _button: Gtk.Button) -> None:
        _LOG.debug("About close button clicked")

    def _on_about_response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        _LOG.debug("About dialog response=%s", response_id)

    def _on_notifications_history_activate(self, _menu_item: Gtk.MenuItem) -> None:
        self._show_notifications_history_dialog()

    def _show_notifications_history_dialog(self) -> None:
        dialog = Gtk.Dialog(title=_("Notification history"), transient_for=self, modal=True)
        dialog.set_default_size(620, 340)
        content = dialog.get_content_area()
        content.set_border_width(8)

        store = Gtk.ListStore(str, str, str)
        for item in self._notification_history:
            store.append([item.get("timestamp", ""), item.get("device", ""), item.get("status", "")])

        tree = Gtk.TreeView(model=store)
        for title, index in [(_("Date/Time"), 0), (_("Device"), 1), (_("Status"), 2)]:
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_resizable(True)
            tree.append_column(column)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(tree)
        content.pack_start(scroll, True, True, 0)

        clear_button = dialog.add_button(_("Clear"), Gtk.ResponseType.APPLY)
        clear_button.set_can_default(False)
        dialog.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        dialog.show_all()

        while True:
            response = dialog.run()
            if response == Gtk.ResponseType.APPLY:
                self._notification_history.clear()
                store.clear()
                continue
            break
        dialog.destroy()

    def _build_accelerators(self) -> Gtk.AccelGroup:
        accel_group = Gtk.AccelGroup()
        self.add_accel_group(accel_group)
        return accel_group

    def _on_devices_updated(self, devices) -> None:
        GLib.idle_add(self._update_ui_devices, devices)

    def _update_ui_devices(self, devices) -> bool:
        self._notify_device_transitions(devices)
        self._merge_sonos_location_suggestions(devices)
        self._rebuild_sidebar(devices)
        self._device_list.set_devices(devices)
        return False

    def _merge_sonos_location_suggestions(self, devices: list[Device]) -> None:
        discovered = self._extract_sonos_room_names(devices)
        if not discovered:
            return
        changed = False
        for value in discovered:
            if value not in self._location_options:
                self._location_options.append(value)
                changed = True
        if not changed:
            return
        self._device_list.set_location_options(self._location_options)
        self._persist_ui_preferences()

    def _extract_sonos_room_names(self, devices: list[Device]) -> list[str]:
        names: list[str] = []
        for device in devices:
            metadata = device.metadata if isinstance(device.metadata, dict) else {}
            xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
            txt_fields = metadata.get("txt") if isinstance(metadata.get("txt"), dict) else {}

            candidates = [
                xml_fields.get("RoomName"),
                xml_fields.get("roomName"),
                metadata.get("RoomName"),
                metadata.get("roomName"),
                txt_fields.get("roomname"),
                txt_fields.get("room_name"),
                txt_fields.get("room"),
            ]
            for raw in candidates:
                if not isinstance(raw, str):
                    continue
                value = raw.strip()
                if value and value not in names:
                    names.append(value)
        return names

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
                self._emit_device_notification(name, "left")
            elif mode == "monitored" and prev_online and prev_monitored:
                name = str(prev.get("name", "Device"))
                self._emit_device_notification(name, "left")
            del self._device_state[key]

        # 2) Online/offline transitions => connected/left.
        for device in devices:
            prev = self._device_state.get(device.key)
            if prev is None:
                # First sighting.
                if bool(device.online) and (mode == "all" or bool(getattr(device, "monitored", False))):
                    name = device.name
                    self._emit_device_notification(name, "connected")
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
                self._emit_device_notification(device.name, "connected")
            elif prev_online is True and device.online is False:
                self._emit_device_notification(device.name, "left")

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
        sidebar_mode = self._sidebar_group_mode()
        counts: dict[str, int] = {}
        bundle_filter_keys: dict[str, str] = {}
        for device in devices:
            if sidebar_mode == "location":
                location = self._device_location_label(device)
                counts[location] = counts.get(location, 0) + 1
                bundle_filter_keys[location] = "location:__none__" if location == _("No location") else f"location:{location}"
            else:
                counts[device.category] = counts.get(device.category, 0) + 1
                bundle_filter_keys[device.category] = device.category
        signature = (sidebar_mode, len(devices), tuple(sorted(counts.items())))
        if signature == self._sidebar_signature:
            return
        self._sidebar_signature = signature

        selected = self._selected_category
        self._is_updating_sidebar = True
        for child in self._sidebar_list.get_children():
            self._sidebar_list.remove(child)
        self._category_rows.clear()

        all_text = _("All Locations") if sidebar_mode == "location" else _("All Types")
        all_label = f"{all_text} ({len(devices)})"
        self._add_sidebar_row(all_label, None)
        for key in sorted(counts.keys(), key=str.lower):
            if sidebar_mode == "location" and key == _("No location"):
                continue
            self._add_sidebar_row(f"{key} ({counts[key]})", bundle_filter_keys[key])
        if sidebar_mode == "location" and _("No location") in counts:
            no_location = _("No location")
            self._add_sidebar_row(f"{no_location} ({counts[no_location]})", bundle_filter_keys[no_location])

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

    def _sidebar_group_mode(self) -> str:
        if self._device_list.view_mode == "icons" and self._device_list.icon_sort_mode == "location":
            return "location"
        return "category"

    def _device_location_label(self, device: Device) -> str:
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        value = metadata.get("user_location")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return _("No location")

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
        sidebar_position = int(self._prefs.get("sidebar_position", 220))
        icon_source_overrides = self._prefs.get("icon_source_overrides")
        custom_icon_overrides = self._prefs.get("custom_icon_overrides")
        icon_sort_mode = str(self._prefs.get("icon_sort_mode", "sorted"))
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
        if isinstance(icon_source_overrides, dict):
            self._device_list.set_icon_source_overrides(icon_source_overrides)
        if isinstance(custom_icon_overrides, dict):
            self._device_list.set_custom_icon_overrides(custom_icon_overrides)
        location_options = self._prefs.get("location_options")
        if isinstance(location_options, list):
            self._location_options = [str(v).strip() for v in location_options if isinstance(v, str) and str(v).strip()]
        if not self._location_options:
            self._location_options = list(_DEFAULT_LOCATION_OPTIONS)
        self._device_list.set_location_options(self._location_options)
        self._device_list.set_icon_sort_mode(icon_sort_mode)
        type_overrides = self._prefs.get("type_overrides")
        if isinstance(type_overrides, dict):
            self._manager.set_type_overrides(type_overrides)
        name_overrides = self._prefs.get("name_overrides")
        if isinstance(name_overrides, dict):
            self._manager.set_name_overrides(name_overrides)
        location_overrides = self._prefs.get("location_overrides")
        if isinstance(location_overrides, dict):
            self._manager.set_location_overrides(location_overrides)
        monitored_overrides = self._prefs.get("monitored_overrides")
        if isinstance(monitored_overrides, dict):
            self._manager.set_monitored_overrides(monitored_overrides)
        last_seen_overrides = self._prefs.get("last_seen_overrides")
        if isinstance(last_seen_overrides, dict):
            self._manager.set_last_seen_overrides(last_seen_overrides)
        monitored_snapshots = self._prefs.get("monitored_device_snapshots")
        if isinstance(monitored_snapshots, list):
            self._manager.restore_monitored_snapshots(monitored_snapshots)
        if view_mode == "list":
            self._list_item.set_active(True)
        else:
            self._icons_item.set_active(True)
        if self._device_list.icon_sort_mode == "appearance":
            self._icons_unsorted_item.set_active(True)
        elif self._device_list.icon_sort_mode == "location":
            self._icons_by_location_item.set_active(True)
        else:
            self._icons_sorted_item.set_active(True)
        self._refresh_icon_sort_menu_state()

        # Restore selected radio option.
        if hasattr(self, "_notif_off_item") and hasattr(self, "_notif_monitored_item") and hasattr(self, "_notif_all_item"):
            self._notif_off_item.set_active(self._notification_mode == "off")
            self._notif_monitored_item.set_active(self._notification_mode == "monitored")
            self._notif_all_item.set_active(self._notification_mode == "all")
        self._refresh_notifications_menu_state()

    def _persist_ui_preferences(self) -> None:
        if self._initializing:
            return
        prefs = {
            "view_mode": self._device_list.view_mode,
            "icon_source_overrides": self._device_list.get_icon_source_overrides(),
            "custom_icon_overrides": self._device_list.get_custom_icon_overrides(),
            "icon_sort_mode": self._device_list.icon_sort_mode,
            "type_overrides": self._manager.get_type_overrides(),
            "name_overrides": self._manager.get_name_overrides(),
            "location_overrides": self._manager.get_location_overrides(),
            "location_options": self._location_options,
            "monitored_overrides": self._manager.get_monitored_overrides(),
            "last_seen_overrides": self._manager.get_last_seen_overrides(),
            "monitored_device_snapshots": self._build_monitored_snapshots(),
            "notification_mode": self._notification_mode,
            "selected_category": self._selected_category,
            "sidebar_position": self._content.get_position(),
        }
        save_ui_preferences(prefs)

    def _build_monitored_snapshots(self) -> list[dict]:
        snapshots: list[dict] = []
        for device in self._manager.devices:
            if not bool(getattr(device, "monitored", False)):
                continue
            last_seen_text = ""
            if hasattr(device.last_seen, "isoformat"):
                try:
                    last_seen_text = device.last_seen.isoformat()
                except Exception:
                    last_seen_text = ""
            snapshots.append(
                {
                    "name": device.name,
                    "ip": device.ip,
                    "port": int(device.port),
                    "type": device.type,
                    "category": device.category,
                    "source": device.source,
                    "url": device.url,
                    "metadata": device.metadata if isinstance(device.metadata, dict) else {},
                    "icon": device.icon,
                    "last_seen": last_seen_text,
                }
            )
        return snapshots

    def _on_destroy(self, *_args) -> None:
        # UX: hide immediately, then complete shutdown work.
        self.hide()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        self._persist_ui_preferences()
        self._manager.stop()

    def _emit_device_notification(self, device_name: str, status: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status_norm = status.strip().lower() if isinstance(status, str) else "status"
        self._notification_history.append(
            {
                "timestamp": timestamp,
                "device": str(device_name),
                "status": status_norm,
            }
        )
        # Keep UI responsive even if desktop notification backend stalls.
        threading.Thread(
            target=send_notification,
            args=("NetNeighbor", str(device_name), f"{device_name} {status_norm}"),
            daemon=True,
        ).start()

    def _refresh_notifications_menu_state(self) -> None:
        enabled = self._notification_mode != "off"
        if hasattr(self, "_notifications_item"):
            self._notifications_item.set_sensitive(enabled)

    def _refresh_icon_sort_menu_state(self) -> None:
        enabled = self._device_list.view_mode == "icons"
        if hasattr(self, "_icons_sorted_item"):
            self._icons_sorted_item.set_sensitive(enabled)
        if hasattr(self, "_icons_unsorted_item"):
            self._icons_unsorted_item.set_sensitive(enabled)
        if hasattr(self, "_icons_by_location_item"):
            self._icons_by_location_item.set_sensitive(enabled)
