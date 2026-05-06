"""Main application window."""

import gi
import logging
from datetime import datetime
import threading

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from discovery.manager import DiscoveryManager
from ui.device_list import DeviceList
from ui.tray_indicator import TrayIndicator
from model.device import Device
from utils.app_version import get_app_version
from utils.location_label import is_plausible_room_location, normalize_location_options
from utils.discovery_cache import load_discovery_cache, save_discovery_cache
from utils.details_payload import format_device_type_for_details
from utils.session_autostart import apply_autostart_pref, autostart_enabled_on_disk
from utils.connect_launcher import normalize_connect_templates
from utils.ui_prefs import load_ui_preferences, save_ui_preferences
from utils.notifications import send_notification
from utils.gtk_dialog import prepare_gtk_dialog
from ui.icons import resolve_app_logo_path

_LOG = logging.getLogger(__name__)


class MainWindow(Gtk.ApplicationWindow):
    def __init__(
        self,
        application: Gtk.Application,
        discovery_manager: DiscoveryManager,
        startup_refresh_seconds: list[int] | None = None,
        information_precedence: list[str] | None = None,
        show_ip_in_device_list: bool = True,
        cli_start_minimized_to_tray: bool = False,
    ) -> None:
        super().__init__(application=application, title="NetNeighbor")
        self._cli_start_minimized_to_tray = bool(cli_start_minimized_to_tray)
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
        self._discovery_cache = load_discovery_cache()
        self._notification_history: list[dict[str, str]] = []
        self._location_options: list[str] = []
        self._type_options: list[dict] = []  # [{"label": str, "slug": str}, ...]
        self._auto_add_discovered_locations: bool = True
        self._defer_persist_cleaned_location_prefs = False
        self._startup_refresh_timer_ids: list[int] = []
        self._startup_refresh_scheduled = False
        self._close_to_tray = bool(self._prefs.get("close_to_tray", True))
        self._start_minimized_to_tray = bool(self._prefs.get("start_minimized_to_tray", False))
        self._start_at_login = bool(self._prefs.get("start_at_login", autostart_enabled_on_disk()))
        self._autostart_onboarding_done = bool(self._prefs.get("autostart_onboarding_done", False))
        self._custom_command_template = str(self._prefs.get("custom_command_template", "") or "")
        self._connect_command_templates = normalize_connect_templates(self._prefs.get("connect_command_templates"))
        self._tray: TrayIndicator | None = None
        self._tray_csd_header: Gtk.HeaderBar | None = None
        self._show_ip_in_device_list = bool(show_ip_in_device_list)
        raw_refresh = startup_refresh_seconds or [10, 30, 60]
        seen_refresh: set[int] = set()
        self._startup_refresh_seconds: list[int] = []
        for value in raw_refresh:
            try:
                delay = int(value)
            except (TypeError, ValueError):
                continue
            if delay <= 0 or delay in seen_refresh:
                continue
            seen_refresh.add(delay)
            self._startup_refresh_seconds.append(delay)

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
        types_item = Gtk.MenuItem.new_with_label(_("Type presets"))
        types_item.connect("activate", self._on_type_presets_activate)
        preferences_menu.append(types_item)
        preferences_menu.append(Gtk.SeparatorMenuItem())

        self._close_tray_prefs_item = Gtk.CheckMenuItem.new_with_label(
            _("Keep running in tray when closing window"),
        )
        self._close_tray_prefs_item.set_active(self._close_to_tray)
        self._close_tray_prefs_item.connect("toggled", self._on_close_tray_pref_toggled)
        preferences_menu.append(self._close_tray_prefs_item)

        self._start_minimized_prefs_item = Gtk.CheckMenuItem.new_with_label(_("Start minimized to tray"))
        self._start_minimized_prefs_item.set_active(self._start_minimized_to_tray)
        self._start_minimized_prefs_item.connect("toggled", self._on_start_minimized_pref_toggled)
        preferences_menu.append(self._start_minimized_prefs_item)

        self._start_at_login_prefs_item = Gtk.CheckMenuItem.new_with_label(_("Start NetNeighbor when logging in"))
        self._start_at_login_prefs_item.set_active(self._start_at_login)
        self._start_at_login_prefs_item.connect("toggled", self._on_start_at_login_pref_toggled)
        preferences_menu.append(self._start_at_login_prefs_item)

        view_menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.ImageMenuItem.new_with_label(_("Quit"))
        quit_item.set_image(Gtk.Image.new_from_icon_name("application-exit-symbolic", Gtk.IconSize.MENU))
        quit_item.set_always_show_image(True)
        quit_item.connect("activate", self._on_quit_activate)
        quit_item.add_accelerator(
            "activate",
            self._build_accelerators(),
            Gdk.KEY_q,
            Gdk.ModifierType.CONTROL_MASK,
            Gtk.AccelFlags.VISIBLE,
        )
        view_menu.append(quit_item)

        self._notifications_item = Gtk.MenuItem.new_with_label(_("Tools"))
        menubar.append(self._notifications_item)
        notifications_menu = Gtk.Menu()
        self._notifications_item.set_submenu(notifications_menu)
        external_apps_item = Gtk.MenuItem.new_with_label(_("External applications…"))
        external_apps_item.connect("activate", self._on_tools_external_apps_activate)
        notifications_menu.append(external_apps_item)
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
            on_set_url_override=self._on_set_url_override,
            on_set_device_commands=self._on_set_device_commands,
            on_set_custom_command=self._on_set_device_custom_command,
            on_set_field_rule=self._on_set_field_rule,
            on_remove_field_rule=self._on_remove_field_rule,
            on_get_field_rules=self._on_get_field_rules,
            information_precedence=information_precedence,
            show_ip_in_device_list=self._show_ip_in_device_list,
        )
        # DeviceList will call this when user chooses Monitor/Unfollow.
        self._content.add2(self._device_list)

        self._manager.set_location_prefs_dirty_callback(self._persist_ui_preferences)
        self._apply_ui_preferences()

        self._manager.add_listener(self._on_devices_updated)
        GLib.idle_add(self._start_discovery_protocols)

        self._initializing = False
        if self._defer_persist_cleaned_location_prefs:
            self._defer_persist_cleaned_location_prefs = False
            self._persist_ui_preferences()
        self.connect("delete-event", self._on_delete_event)
        self.connect("destroy", self._on_destroy)
        self._install_fullscreen_accel()
        # set_titlebar() must be called before the window is realized/shown.
        self._ensure_tray()
        self._apply_tray_window_decorations()
        self.show_all()
        GLib.idle_add(self._idle_post_show)

    def _idle_post_show(self) -> bool:
        ran_first_run_dialog = self._maybe_show_autostart_onboarding()
        if self._tray is not None and self._tray.available:
            if (
                ran_first_run_dialog
                or self._start_minimized_to_tray
                or self._cli_start_minimized_to_tray
            ):
                self.hide()
        return False

    def _maybe_show_autostart_onboarding(self) -> bool:
        """First-run prompt: offer login autostart (checkbox on by default); always mark onboarding done.

        Returns True if the dialog was shown (first run); False if onboarding was already completed.
        """
        if self._autostart_onboarding_done:
            return False

        dlg = Gtk.Dialog(
            title=_("Session startup"),
            transient_for=self,
            modal=True,
            destroy_with_parent=True,
        )
        prepare_gtk_dialog(dlg)
        dlg.add_button(_("Not now"), Gtk.ResponseType.CANCEL)
        dlg.add_button(_("Save"), Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)

        area = dlg.get_content_area()
        area.set_spacing(10)
        area.set_border_width(12)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        head = Gtk.Label(label=_("Run NetNeighbor in the background?"))
        head.set_halign(Gtk.Align.START)
        body = Gtk.Label(
            label=_(
                "You can keep NetNeighbor in the system tray when you close the window. "
                "Starting it with your session keeps LAN discovery running in the background. "
                "You can change this anytime under View → Preferences."
            ),
        )
        body.set_line_wrap(True)
        body.set_max_width_chars(52)
        body.set_halign(Gtk.Align.START)

        cb = Gtk.CheckButton(label=_("Launch NetNeighbor when I log in"))
        cb.set_active(True)

        vbox.pack_start(head, False, False, 0)
        vbox.pack_start(body, False, False, 0)
        if self._tray is not None and self._tray.available:
            tray_hint = Gtk.Label(
                label=_(
                    "After you close this dialog, the main window hides to the system tray — "
                    "click the NetNeighbor tray icon to open it (recommended after install)."
                ),
            )
            tray_hint.set_line_wrap(True)
            tray_hint.set_max_width_chars(52)
            tray_hint.set_halign(Gtk.Align.START)
            tray_hint.get_style_context().add_class("dim-label")
            vbox.pack_start(tray_hint, False, False, 0)
        vbox.pack_start(cb, False, False, 0)
        area.add(vbox)

        dlg.show_all()
        response = dlg.run()
        dlg.destroy()

        self._autostart_onboarding_done = True
        if response == Gtk.ResponseType.OK:
            self._start_at_login = bool(cb.get_active())
            apply_autostart_pref(self._start_at_login)
            if hasattr(self, "_start_at_login_prefs_item"):
                self._start_at_login_prefs_item.handler_block_by_func(self._on_start_at_login_pref_toggled)
                self._start_at_login_prefs_item.set_active(self._start_at_login)
                self._start_at_login_prefs_item.handler_unblock_by_func(self._on_start_at_login_pref_toggled)
        self._persist_ui_preferences()
        return True

    def _apply_tray_window_decorations(self) -> None:
        """With tray + close-to-tray: header bar = maximize + close (no minimize; F11 = fullscreen)."""
        want = self._close_to_tray
        if want:
            self._ensure_tray()
            want = self._tray is not None and self._tray.available
        if want:
            if self._tray_csd_header is None:
                hb = Gtk.HeaderBar()
                hb.set_show_close_button(True)
                hb.set_decoration_layout(":maximize,close")
                hb.set_title(self.get_title())
                self.set_titlebar(hb)
                self._tray_csd_header = hb
            else:
                self._tray_csd_header.set_title(self.get_title())
        else:
            if self._tray_csd_header is not None:
                self.set_titlebar(None)
                self._tray_csd_header = None

    def _ensure_tray(self) -> None:
        if self._tray is not None:
            return
        icon_name = "io.esp3d.netneighbor-tray"
        self._tray = TrayIndicator(
            app_id="io.esp3d.netneighbor",
            icon_name=icon_name,
            tooltip=_("NetNeighbor — LAN discovery"),
            menu_open_label=_("Open NetNeighbor"),
            menu_quit_label=_("Quit"),
            menu_minimize_label=_("Minimize to tray"),
            on_minimize_to_tray=self._hide_main_window_to_tray,
            on_open=self._present_main_window,
            on_quit=self._quit_application,
        )
        if not self._tray.available:
            self._tray = None
            _LOG.warning(
                "No system tray available (install gir1.2-ayatanaappindicator3-0.1 or gir1.2-appindicator3-0.1)"
            )
        else:
            self._connect_tray_minimize_menu_state()

    def _connect_tray_minimize_menu_state(self) -> None:
        """Enable \"Minimize to tray\" only while the window is mapped (visible)."""
        if getattr(self, "_tray_minimize_visibility_connected", False):
            return
        self._tray_minimize_visibility_connected = True

        def _sync(_obj=None, *_args) -> None:
            if self._tray is not None:
                self._tray.set_minimize_sensitive(self.get_visible())

        self.connect("notify::visible", _sync)
        _sync()

    def _hide_main_window_to_tray(self) -> None:
        self.hide()

    def _present_main_window(self) -> None:
        self.show_all()
        self.deiconify()
        self.present()
        try:
            event_time = Gtk.get_current_event_time()
            if event_time == 0:
                event_time = int(__import__("time").monotonic() * 1000) & 0xFFFFFFFF
            self.present_with_time(event_time)
        except Exception:
            pass
        self.grab_focus()

    def _quit_application(self, *_args) -> None:
        app = self.get_application()
        if app is not None:
            app.quit()
        else:
            self.destroy()

    def _on_quit_activate(self, *_args) -> None:
        self._quit_application()

    def _on_close_tray_pref_toggled(self, item: Gtk.CheckMenuItem) -> None:
        if self._initializing:
            return
        self._close_to_tray = bool(item.get_active())
        self._persist_ui_preferences()
        self._apply_tray_window_decorations()

    def _on_start_minimized_pref_toggled(self, item: Gtk.CheckMenuItem) -> None:
        if self._initializing:
            return
        self._start_minimized_to_tray = bool(item.get_active())
        self._persist_ui_preferences()

    def _on_start_at_login_pref_toggled(self, item: Gtk.CheckMenuItem) -> None:
        if self._initializing:
            return
        self._start_at_login = bool(item.get_active())
        apply_autostart_pref(self._start_at_login)
        self._persist_ui_preferences()

    def _on_delete_event(self, _widget: Gtk.Widget, _event: Gdk.Event) -> bool:
        self._ensure_tray()
        if self._close_to_tray and self._tray is not None and self._tray.available:
            self.hide()
            return True
        return False

    def _start_discovery_protocols(self) -> bool:
        self._manager.start()
        # Wake slow probes (WSD/nmb) once startup threads are running — feels closer to OS network browsers.
        try:
            self._manager.refresh()
        except Exception:
            _LOG.debug("Post-start discovery refresh failed", exc_info=True)
        self._schedule_startup_refreshes()
        return False

    def _schedule_startup_refreshes(self) -> None:
        if self._startup_refresh_scheduled:
            return
        self._startup_refresh_scheduled = True
        # Recover late/missed SSDP responses shortly after app startup.
        for delay_seconds in self._startup_refresh_seconds:
            timer_id = GLib.timeout_add_seconds(delay_seconds, self._run_startup_refresh_once, delay_seconds)
            self._startup_refresh_timer_ids.append(timer_id)

    def _run_startup_refresh_once(self, delay_seconds: int) -> bool:
        # Always wake discovery protocols — visibility must not skip probes (background discovery).
        try:
            _LOG.debug("Startup auto-refresh triggered at +%ss", delay_seconds)
            self._manager.refresh()
        except Exception:
            _LOG.debug("Startup auto-refresh failed at +%ss", delay_seconds, exc_info=True)
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

    def _on_set_url_override(self, source: str, ip: str, port: int, url: str | None) -> None:
        self._manager.set_device_url_override(source, ip, port, url)
        self._persist_ui_preferences()

    def _on_set_device_commands(self, source: str, ip: str, port: int, commands: list) -> None:
        self._manager.set_device_commands(source, ip, port, commands)
        self._persist_ui_preferences()

    def _on_set_device_custom_command(self, source: str, ip: str, port: int, cmd: str | None) -> None:
        self._manager.set_device_custom_command(source, ip, port, cmd)
        self._persist_ui_preferences()

    def _on_set_field_rule(self, source: str, ip: str, port: int, target: str, field_path: str) -> None:
        self._manager.set_device_field_mapping_rule(source, ip, port, target, field_path)
        self._persist_ui_preferences()

    def _on_remove_field_rule(self, source: str, ip: str, port: int, target: str, field_path: str | None) -> None:
        self._manager.remove_device_field_mapping_rule(source, ip, port, target, field_path)
        self._persist_ui_preferences()

    def _on_get_field_rules(self, source: str, ip: str, port: int) -> dict[str, list[str]]:
        for device in self._manager.devices:
            if device.source == source and device.ip == ip and int(device.port) == int(port):
                return self._manager.get_device_field_mapping_rules(device)
        return {}

    def _default_location_preset_strings(self) -> list[str]:
        """Translated starter presets when the saved list is still empty (lazy init)."""
        return normalize_location_options(
            [
                _("Office"),
                _("Room"),
                _("Living room"),
                _("Kitchen"),
                _("Workshop"),
                _("Garage"),
            ]
        )

    def _on_locations_presets_activate(self, _menu_item: Gtk.MenuItem) -> None:
        dialog = Gtk.Dialog(title=_("Location presets"), transient_for=self, modal=True)
        prepare_gtk_dialog(dialog)
        dialog.set_default_size(460, 420)
        content = dialog.get_content_area()
        content.set_border_width(8)
        label = Gtk.Label(
            label=_("Manage location presets."),
            xalign=0.0,
        )
        label.set_line_wrap(True)
        content.pack_start(label, False, False, 4)

        auto_add_chk = Gtk.CheckButton(
            label=_("Automatically add locations discovered on the network to this list"),
        )
        auto_add_chk.set_active(self._auto_add_discovered_locations)
        auto_add_chk.set_tooltip_text(
            _(
                "When enabled, room names and similar strings from devices "
                "(for example some speakers) can be added to the preset list as they are seen."
            )
        )
        content.pack_start(auto_add_chk, False, False, 4)

        presets_store = Gtk.ListStore(str)
        initial_presets = list(self._location_options)
        if not initial_presets:
            initial_presets = self._default_location_preset_strings()
        for value in initial_presets:
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
            prepare_gtk_dialog(prompt)
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
            self._auto_add_discovered_locations = auto_add_chk.get_active()
            previous_options = list(self._location_options)
            self._location_options = normalize_location_options(_existing_values())
            self._device_list.set_location_options(self._location_options)
            self._apply_location_preset_changes(previous_options, self._location_options, rename_map)
            self._persist_ui_preferences()
        dialog.destroy()

    def _default_type_options(self) -> list[dict]:
        """Default list of device type presets (translatable labels with stable slugs)."""
        return [
            {"label": _("NAS"), "slug": "nas"},
            {"label": _("Computer"), "slug": "computer"},
            {"label": _("Router"), "slug": "router"},
            {"label": _("Media server"), "slug": "mediaserver"},
            {"label": _("Printer"), "slug": "printer"},
            {"label": _("Multifunction printer"), "slug": "multifunction_printer"},
            {"label": _("Printer (network / IPP)"), "slug": "networkprinter"},
            {"label": _("SmartSpeaker"), "slug": "smartspeaker"},
            {"label": _("SmartTV"), "slug": "smarttv"},
            {"label": _("SmartDevice"), "slug": "smartdevice"},
            {"label": _("Camera"), "slug": "camera"},
            {"label": _("HomeAppliance"), "slug": "homeappliance"},
            {"label": _("CNC"), "slug": "cnc"},
            {"label": _("3D printer"), "slug": "3dprinter"},
        ]

    def _on_type_presets_activate(self, _menu_item: Gtk.MenuItem) -> None:
        dialog = Gtk.Dialog(title=_("Type presets"), transient_for=self, modal=True)
        prepare_gtk_dialog(dialog)
        dialog.set_default_size(500, 440)
        content = dialog.get_content_area()
        content.set_border_width(8)
        label = Gtk.Label(
            label=_("Manage device type presets shown in the right-click menu."),
            xalign=0.0,
        )
        label.set_line_wrap(True)
        content.pack_start(label, False, False, 4)

        # Two-column store: display label | slug
        presets_store = Gtk.ListStore(str, str)
        initial_presets = list(self._type_options)
        if not initial_presets:
            initial_presets = self._default_type_options()
        for entry in initial_presets:
            if isinstance(entry, dict):
                lbl = str(entry.get("label", "")).strip()
                slg = str(entry.get("slug", "")).strip()
                if lbl and slg:
                    presets_store.append([lbl, slg])
        presets_view = Gtk.TreeView(model=presets_store)
        presets_view.set_headers_visible(True)
        presets_view.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        presets_view.append_column(Gtk.TreeViewColumn(_("Label"), Gtk.CellRendererText(), text=0))
        presets_view.append_column(Gtk.TreeViewColumn(_("Type ID"), Gtk.CellRendererText(), text=1))
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
        restore_button = Gtk.Button.new_with_label(_("Restore defaults"))
        controls.pack_start(add_button, False, False, 0)
        controls.pack_start(rename_button, False, False, 0)
        controls.pack_start(remove_button, False, False, 0)
        controls.pack_end(restore_button, False, False, 0)
        content.pack_start(controls, False, False, 2)

        def _prompt_type(title: str, initial_label: str = "", initial_slug: str = "") -> tuple[str, str] | None:
            prompt = Gtk.Dialog(title=title, transient_for=dialog, modal=True)
            prepare_gtk_dialog(prompt)
            prompt.set_default_size(340, -1)
            area = prompt.get_content_area()
            area.set_border_width(8)
            grid = Gtk.Grid()
            grid.set_column_spacing(8)
            grid.set_row_spacing(6)
            lbl_label = Gtk.Label(label=_("Label:"), xalign=1.0)
            lbl_slug = Gtk.Label(label=_("Type ID:"), xalign=1.0)
            entry_label = Gtk.Entry()
            entry_slug = Gtk.Entry()
            entry_label.set_text(initial_label)
            entry_label.select_region(0, -1)
            entry_slug.set_text(initial_slug)
            entry_slug.set_placeholder_text(_("e.g. nas, router, camera…"))
            grid.attach(lbl_label, 0, 0, 1, 1)
            grid.attach(entry_label, 1, 0, 1, 1)
            grid.attach(lbl_slug, 0, 1, 1, 1)
            grid.attach(entry_slug, 1, 1, 1, 1)
            area.pack_start(grid, False, False, 0)
            prompt.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
            prompt.add_button(_("OK"), Gtk.ResponseType.OK)
            prompt.show_all()
            resp = prompt.run()
            result = None
            if resp == Gtk.ResponseType.OK:
                lv = entry_label.get_text().strip()
                sv = entry_slug.get_text().strip().lower().replace(" ", "_")
                if lv and sv:
                    result = (lv, sv)
            prompt.destroy()
            return result

        def _selected_iter():
            _model, tree_iter = presets_view.get_selection().get_selected()
            return tree_iter

        def _existing_slugs() -> set[str]:
            slugs: set[str] = set()
            it = presets_store.get_iter_first()
            while it is not None:
                slugs.add(presets_store.get_value(it, 1))
                it = presets_store.iter_next(it)
            return slugs

        def _on_add_clicked(_btn: Gtk.Button) -> None:
            result = _prompt_type(_("Add type preset"))
            if not result:
                return
            new_label, new_slug = result
            if new_slug in _existing_slugs():
                return
            presets_store.append([new_label, new_slug])

        def _on_rename_clicked(_btn: Gtk.Button) -> None:
            it = _selected_iter()
            if it is None:
                return
            old_label = presets_store.get_value(it, 0)
            old_slug = presets_store.get_value(it, 1)
            result = _prompt_type(_("Rename type preset"), old_label, old_slug)
            if not result:
                return
            new_label, new_slug = result
            if new_slug != old_slug and new_slug in _existing_slugs():
                return
            presets_store.set_value(it, 0, new_label)
            presets_store.set_value(it, 1, new_slug)

        def _on_remove_clicked(_btn: Gtk.Button) -> None:
            it = _selected_iter()
            if it is None:
                return
            presets_store.remove(it)

        def _on_restore_clicked(_btn: Gtk.Button) -> None:
            presets_store.clear()
            for entry in self._default_type_options():
                presets_store.append([entry["label"], entry["slug"]])

        add_button.connect("clicked", _on_add_clicked)
        rename_button.connect("clicked", _on_rename_clicked)
        remove_button.connect("clicked", _on_remove_clicked)
        restore_button.connect("clicked", _on_restore_clicked)

        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Save"), Gtk.ResponseType.OK)
        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            new_options: list[dict] = []
            it = presets_store.get_iter_first()
            while it is not None:
                lv = presets_store.get_value(it, 0)
                sv = presets_store.get_value(it, 1)
                if isinstance(lv, str) and isinstance(sv, str) and lv.strip() and sv.strip():
                    new_options.append({"label": lv.strip(), "slug": sv.strip()})
                it = presets_store.iter_next(it)
            self._type_options = new_options
            self._device_list.set_type_options([(e["label"], e["slug"]) for e in self._type_options])
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

    def _on_tools_external_apps_activate(self, _menu_item: Gtk.MenuItem) -> None:
        dialog = Gtk.Dialog(title=_("External applications"), transient_for=self, modal=True)
        prepare_gtk_dialog(dialog)
        dialog.set_default_size(580, 520)
        outer = dialog.get_content_area()
        outer.set_border_width(8)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_overlay_scrolling(False)
        scroll.set_min_content_height(380)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(12)
        box.set_margin_top(4)
        box.set_margin_bottom(8)

        from utils.connect_launcher import default_connect_command_templates as _defaults
        _cmd_defaults = _defaults()
        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(6)
        entry_by_key: dict[str, Gtk.Entry] = {}
        rows = [
            ("http", _("HTTP")),
            ("https", _("HTTPS")),
            ("smb", _("SMB")),
            ("ftp", _("FTP")),
            ("ssh", _("SSH")),
            ("telnet", _("Telnet")),
            ("sftp", _("SFTP")),
        ]
        for i, (key, title) in enumerate(rows):
            lab = Gtk.Label(label=title + ":", xalign=1.0)
            ent = Gtk.Entry()
            ent.set_hexpand(True)
            ent.set_text(self._connect_command_templates.get(key, ""))
            ent.set_placeholder_text(_("Empty = system default"))
            reset_btn = Gtk.Button.new_with_label(_("Reset"))
            reset_btn.set_tooltip_text(_("Restore default command"))
            _default_val = _cmd_defaults.get(key, "")

            def _on_reset_scheme(_b, _e=ent, _v=_default_val, _w=dialog):
                dlg = Gtk.MessageDialog(
                    transient_for=_w, modal=True,
                    message_type=Gtk.MessageType.QUESTION,
                    buttons=Gtk.ButtonsType.NONE,
                    text=_("Reset this command to its default value?"),
                )
                dlg.add_button(_("No"), Gtk.ResponseType.NO)
                dlg.add_button(_("Yes"), Gtk.ResponseType.YES)
                dlg.set_default_response(Gtk.ResponseType.NO)
                if dlg.run() == Gtk.ResponseType.YES:
                    _e.set_text(_v)
                dlg.destroy()

            reset_btn.connect("clicked", _on_reset_scheme)
            grid.attach(lab, 0, i, 1, 1)
            grid.attach(ent, 1, i, 1, 1)
            grid.attach(reset_btn, 2, i, 1, 1)
            entry_by_key[key] = ent

        custom_ent = Gtk.Entry()
        custom_ent.set_hexpand(True)
        custom_ent.set_text(self._custom_command_template)
        custom_ent.set_placeholder_text(_("Empty = disabled"))
        custom_clear_btn = Gtk.Button.new_with_label(_("Clear"))
        custom_clear_btn.set_tooltip_text(_("Remove the global custom command"))

        def _on_clear_custom(_b, _e=custom_ent, _w=dialog):
            dlg = Gtk.MessageDialog(
                transient_for=_w, modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.NONE,
                text=_("Clear the global custom command?"),
            )
            dlg.add_button(_("No"), Gtk.ResponseType.NO)
            dlg.add_button(_("Yes"), Gtk.ResponseType.YES)
            dlg.set_default_response(Gtk.ResponseType.NO)
            if dlg.run() == Gtk.ResponseType.YES:
                _e.set_text("")
            dlg.destroy()

        custom_clear_btn.connect("clicked", _on_clear_custom)
        custom_row = len(rows)
        grid.attach(Gtk.Label(label=_("Custom:"), xalign=1.0), 0, custom_row, 1, 1)
        grid.attach(custom_ent, 1, custom_row, 1, 1)
        grid.attach(custom_clear_btn, 2, custom_row, 1, 1)

        box.pack_start(grid, False, False, 0)

        hint = Gtk.Label(xalign=0.0)
        hint.set_markup(
            "<small>"
            + "<b>{url}</b> " + _("full address") + "   "
            + "<b>{ip}</b> " + _("device IP") + "   "
            + "<b>{port}</b> " + _("service port") + "   "
            + "<b>{name}</b> " + _("device name") + "   "
            + "<b>{type}</b> " + _("device type") + "   "
            + "<b>{category}</b> " + _("category")
            + "</small>"
        )
        hint.set_line_wrap(True)
        hint.set_margin_start(2)
        hint.set_margin_top(4)
        box.pack_start(hint, False, False, 0)

        scroll.add(box)
        outer.pack_start(scroll, True, True, 0)
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Save"), Gtk.ResponseType.OK)
        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            for key, ent in entry_by_key.items():
                self._connect_command_templates[key] = ent.get_text()
            self._custom_command_template = custom_ent.get_text().strip()
            self._device_list.set_connect_command_templates(self._connect_command_templates)
            self._device_list.set_custom_command_template(self._custom_command_template)
            self._persist_ui_preferences()
        dialog.destroy()

    @staticmethod
    def _relabel_about_dialog_buttons(dialog: Gtk.AboutDialog) -> None:
        """Relabel AboutDialog built-in buttons with translated strings.

        GTK creates these buttons internally (labels like 'C_redits', '_License',
        '_Close' with mnemonic markers) and translates them via its own catalog.
        On setups where GTK's locale is not picked up we relabel them explicitly.
        """
        _label_map = {
            "credits": _("Credits"),
            "license": _("License"),
            "close":   _("Close"),
        }
        try:
            action_area = dialog.get_action_area()
        except Exception:
            return
        if action_area is None:
            return
        for btn in action_area.get_children():
            if not isinstance(btn, (Gtk.Button,)):
                continue
            raw = btn.get_label() or ""
            # Strip GTK mnemonic underscore (e.g. "C_redits" → "Credits")
            key = raw.replace("_", "").lower()
            if key in _label_map:
                btn.set_label(_label_map[key])

    def _on_about_activate(self, _menu_item: Gtk.MenuItem) -> None:
        _LOG.debug("About menu clicked")
        dialog = Gtk.AboutDialog(transient_for=self, modal=True)
        dialog.set_default_response(Gtk.ResponseType.CLOSE)
        # Relabel built-in buttons after they are realized (GTK creates them lazily)
        dialog.connect("realize", self._relabel_about_dialog_buttons)
        close_button = dialog.get_widget_for_response(Gtk.ResponseType.CLOSE)
        if close_button is not None:
            close_button.connect("clicked", self._on_about_close_clicked)
            close_button.set_receives_default(True)
            close_button.grab_default()
            close_button.grab_focus()
            dialog.set_focus(close_button)
        logo_path = resolve_app_logo_path()
        if logo_path is not None:
            try:
                from gi.repository import GdkPixbuf
                logo = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(logo_path), 128, 128, True)
                dialog.set_logo(logo)
            except Exception:
                _LOG.debug("Could not load About dialog logo from %s", logo_path, exc_info=True)
        dialog.set_program_name("NetNeighbor")
        dialog.set_version(get_app_version())
        dialog.set_authors(["Luc"])
        dialog.set_comments(_("Linux network neighborhood for SSDP/mDNS discovery."))
        dialog.add_credit_section(
            _("Python libraries"),
            [
                "PyGObject — GTK 3 bindings  https://pygobject.gnome.org",
                "zeroconf — mDNS/DNS-SD discovery  https://github.com/python-zeroconf/python-zeroconf",
                "WSDiscovery — WS-Discovery (Windows devices)  https://github.com/andreikop/python-ws-discovery",
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
        prepare_gtk_dialog(dialog)
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

    def _install_fullscreen_accel(self) -> None:
        """F11 toggles fullscreen — useful for the icon grid."""

        def _on_accel(_group: Gtk.AccelGroup, _acc: object, *_rest: object) -> bool:
            if self.is_fullscreen():
                self.unfullscreen()
            else:
                self.fullscreen()
            return True

        ag = Gtk.AccelGroup()
        self.add_accel_group(ag)
        ag.connect(
            Gdk.KEY_F11,
            Gdk.ModifierType(0),
            Gtk.AccelFlags.VISIBLE,
            _on_accel,
        )

    def _build_accelerators(self) -> Gtk.AccelGroup:
        accel_group = Gtk.AccelGroup()
        self.add_accel_group(accel_group)
        return accel_group

    def _on_devices_updated(self, devices) -> None:
        GLib.idle_add(self._update_ui_devices, devices)

    def _update_ui_devices(self, devices) -> bool:
        self._notify_device_transitions(devices)
        self._merge_sonos_location_suggestions(devices)
        self._device_list.set_devices(devices)
        self._rebuild_sidebar(devices)
        return False

    def _merge_sonos_location_suggestions(self, devices: list[Device]) -> None:
        if not self._auto_add_discovered_locations:
            return
        discovered = self._extract_sonos_room_names(devices)
        if not discovered:
            return
        changed = False
        for value in discovered:
            if is_plausible_room_location(value) and value not in self._location_options:
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

            def txt_ci(txt: dict, keys: tuple[str, ...]) -> list[str]:
                indexed = {str(k).lower(): v for k, v in txt.items() if isinstance(k, str)}
                out: list[str] = []
                for lk in keys:
                    v = indexed.get(lk.lower())
                    if isinstance(v, str) and v.strip() and v.strip() not in out:
                        out.append(v.strip())
                return out

            candidates = [
                xml_fields.get("RoomName"),
                xml_fields.get("roomName"),
                metadata.get("RoomName"),
                metadata.get("roomName"),
                txt_fields.get("roomname"),
                txt_fields.get("room_name"),
                txt_fields.get("room"),
            ]
            candidates.extend(
                txt_ci(
                    txt_fields,
                    ("location", "locationname", "location_name", "zonename", "zone_name"),
                )
            )
            for svc in metadata.get("services") if isinstance(metadata.get("services"), list) else []:
                if not isinstance(svc, dict):
                    continue
                stxt = svc.get("txt") if isinstance(svc.get("txt"), dict) else {}
                candidates.extend(txt_ci(stxt, ("location", "room_name", "room", "zonename")))
            for raw in candidates:
                if not isinstance(raw, str):
                    continue
                value = raw.strip()
                if value and is_plausible_room_location(value) and value not in names:
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
        # Count merged host bundles, not raw Device rows — otherwise SSDP+mDNS pairs
        # inflate category totals (e.g. "Unknown" shows more than visible tiles).
        bundles = self._device_list.build_bundles(devices) if devices else []
        for bundle in bundles:
            if sidebar_mode == "location":
                location = self._device_list.bundle_location_label(bundle)
                counts[location] = counts.get(location, 0) + 1
                bundle_filter_keys[location] = "location:__none__" if location == _("No location") else f"location:{location}"
            else:
                slug = (bundle.primary.type or "unknown").strip().lower()
                counts[slug] = counts.get(slug, 0) + 1
                bundle_filter_keys[slug] = slug
        signature = (sidebar_mode, len(bundles), tuple(sorted(counts.items())))
        if signature == self._sidebar_signature:
            return
        self._sidebar_signature = signature

        selected = self._selected_category
        self._is_updating_sidebar = True
        for child in self._sidebar_list.get_children():
            self._sidebar_list.remove(child)
        self._category_rows.clear()

        type_slug_labels: dict[str, str] = {}
        if sidebar_mode != "location":
            for b in bundles:
                slug = (b.primary.type or "unknown").strip().lower()
                if slug not in type_slug_labels:
                    type_slug_labels[slug] = format_device_type_for_details(b.primary)

        all_text = _("All Locations") if sidebar_mode == "location" else _("All Types")
        all_label = f"{all_text} ({len(bundles)})"
        self._add_sidebar_row(all_label, None)

        for key in sorted(
            counts.keys(),
            key=lambda k: str(k).lower()
            if sidebar_mode == "location"
            else type_slug_labels.get(k, k).lower(),
        ):
            if sidebar_mode == "location" and key == _("No location"):
                continue
            label = key if sidebar_mode == "location" else type_slug_labels.get(key, key)
            self._add_sidebar_row(f"{label} ({counts[key]})", bundle_filter_keys[key])
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
            raw_opts = [str(v).strip() for v in location_options if isinstance(v, str) and str(v).strip()]
            self._location_options = normalize_location_options(raw_opts)
            if raw_opts != self._location_options:
                self._defer_persist_cleaned_location_prefs = True
        else:
            self._location_options = []
        aad = self._prefs.get("auto_add_discovered_locations")
        if aad is True or aad is False:
            self._auto_add_discovered_locations = aad
        else:
            self._auto_add_discovered_locations = True
        self._device_list.set_location_options(self._location_options)
        type_options_raw = self._prefs.get("type_options")
        if isinstance(type_options_raw, list):
            self._type_options = [
                {"label": str(e.get("label", "")).strip(), "slug": str(e.get("slug", "")).strip()}
                for e in type_options_raw
                if isinstance(e, dict)
                and str(e.get("label", "")).strip()
                and str(e.get("slug", "")).strip()
            ]
        else:
            self._type_options = []
        self._device_list.set_type_options([(e["label"], e["slug"]) for e in self._type_options])
        self._device_list.set_icon_sort_mode(icon_sort_mode)
        type_overrides = self._prefs.get("type_overrides")
        if isinstance(type_overrides, dict):
            self._manager.set_type_overrides(type_overrides)
        name_overrides = self._prefs.get("name_overrides")
        if isinstance(name_overrides, dict):
            self._manager.set_name_overrides(name_overrides)
        url_overrides = self._prefs.get("url_overrides")
        if isinstance(url_overrides, dict):
            self._manager.set_url_overrides(url_overrides)
        # Load device_commands (new format) with fallback to legacy endpoint_overrides
        device_commands_raw = self._prefs.get("device_commands") or self._prefs.get("endpoint_overrides")
        if isinstance(device_commands_raw, dict):
            self._manager.set_device_commands_overrides(device_commands_raw)
        custom_command_overrides = self._prefs.get("custom_command_overrides")
        if isinstance(custom_command_overrides, dict):
            self._manager.set_custom_command_overrides(custom_command_overrides)
        location_overrides = self._prefs.get("location_overrides")
        if isinstance(location_overrides, dict):
            if any(
                isinstance(v, str)
                and str(v).strip()
                and not is_plausible_room_location(str(v).strip())
                for v in location_overrides.values()
            ):
                self._defer_persist_cleaned_location_prefs = True
            cleaned_overrides = {
                str(k): str(v).strip()
                for k, v in location_overrides.items()
                if isinstance(k, str)
                and isinstance(v, str)
                and str(v).strip()
                and is_plausible_room_location(str(v).strip())
            }
            self._manager.set_location_overrides(cleaned_overrides)
        field_mapping_rules = self._prefs.get("field_mapping_rules")
        if isinstance(field_mapping_rules, dict):
            self._manager.set_field_mapping_rules(field_mapping_rules)
        monitored_overrides = self._prefs.get("monitored_overrides")
        if isinstance(monitored_overrides, dict):
            self._manager.set_monitored_overrides(monitored_overrides)
        last_seen_overrides = self._discovery_cache.get("last_seen_overrides")
        if isinstance(last_seen_overrides, dict):
            self._manager.set_last_seen_overrides(last_seen_overrides)
        monitored_snapshots = self._discovery_cache.get("monitored_device_snapshots")
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

        self._close_to_tray = bool(self._prefs.get("close_to_tray", True))
        self._start_minimized_to_tray = bool(self._prefs.get("start_minimized_to_tray", False))
        self._start_at_login = bool(self._prefs.get("start_at_login", autostart_enabled_on_disk()))
        self._autostart_onboarding_done = bool(self._prefs.get("autostart_onboarding_done", False))
        self._custom_command_template = str(self._prefs.get("custom_command_template", "") or "")
        self._connect_command_templates = normalize_connect_templates(self._prefs.get("connect_command_templates"))
        self._device_list.set_custom_command_template(self._custom_command_template)
        self._device_list.set_connect_command_templates(self._connect_command_templates)
        if hasattr(self, "_close_tray_prefs_item") and hasattr(self, "_start_minimized_prefs_item"):
            self._close_tray_prefs_item.handler_block_by_func(self._on_close_tray_pref_toggled)
            self._start_minimized_prefs_item.handler_block_by_func(self._on_start_minimized_pref_toggled)
            self._close_tray_prefs_item.set_active(self._close_to_tray)
            self._start_minimized_prefs_item.set_active(self._start_minimized_to_tray)
            self._close_tray_prefs_item.handler_unblock_by_func(self._on_close_tray_pref_toggled)
            self._start_minimized_prefs_item.handler_unblock_by_func(self._on_start_minimized_pref_toggled)
        if hasattr(self, "_start_at_login_prefs_item"):
            self._start_at_login_prefs_item.handler_block_by_func(self._on_start_at_login_pref_toggled)
            self._start_at_login_prefs_item.set_active(self._start_at_login)
            self._start_at_login_prefs_item.handler_unblock_by_func(self._on_start_at_login_pref_toggled)
        apply_autostart_pref(self._start_at_login)
        if not self._initializing:
            self._apply_tray_window_decorations()

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
            "url_overrides": self._manager.get_url_overrides(),
            "device_commands": self._manager.get_device_commands_overrides(),
            "custom_command_overrides": self._manager.get_custom_command_overrides(),
            "location_overrides": self._manager.get_location_overrides(),
            "field_mapping_rules": self._manager.get_field_mapping_rules(),
            "location_options": normalize_location_options(self._location_options),
            "auto_add_discovered_locations": bool(self._auto_add_discovered_locations),
            "type_options": list(self._type_options),
            "monitored_overrides": self._manager.get_monitored_overrides(),
            "notification_mode": self._notification_mode,
            "selected_category": self._selected_category,
            "sidebar_position": self._content.get_position(),
            "close_to_tray": bool(self._close_to_tray),
            "start_minimized_to_tray": bool(self._start_minimized_to_tray),
            "start_at_login": bool(self._start_at_login),
            "autostart_onboarding_done": bool(self._autostart_onboarding_done),
            "custom_command_template": str(self._custom_command_template or ""),
            "connect_command_templates": dict(self._connect_command_templates),
        }
        save_ui_preferences(prefs)
        self._discovery_cache = {
            "last_seen_overrides": self._manager.get_last_seen_overrides(),
            "monitored_device_snapshots": self._build_monitored_snapshots(),
        }
        save_discovery_cache(self._discovery_cache)

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
        for timer_id in self._startup_refresh_timer_ids:
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
        self._startup_refresh_timer_ids.clear()
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
        pass

    def _refresh_icon_sort_menu_state(self) -> None:
        enabled = self._device_list.view_mode == "icons"
        if hasattr(self, "_icons_sorted_item"):
            self._icons_sorted_item.set_sensitive(enabled)
        if hasattr(self, "_icons_unsorted_item"):
            self._icons_unsorted_item.set_sensitive(enabled)
        if hasattr(self, "_icons_by_location_item"):
            self._icons_by_location_item.set_sensitive(enabled)
