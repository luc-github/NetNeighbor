"""Read-only protocol details dialog."""

import gi
import ipaddress
import logging
import re
from collections.abc import Callable
from pathlib import Path
import subprocess
from typing import Any

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from utils.gtk_dialog import prepare_gtk_dialog

_LOG = logging.getLogger(__name__)

_HOSTNAME_RE = re.compile(
    r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*\.?$'
)
_IPV4_LIKE_RE = re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$')


def _is_valid_ip_or_host(value: str) -> bool:
    """Return True if value is empty, a valid IP address, or a valid hostname."""
    if not value:
        return True
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        pass
    # Reject anything that looks like a dotted-quad but failed ip_address()
    if _IPV4_LIKE_RE.match(value):
        return False
    return bool(_HOSTNAME_RE.match(value))


class DeviceDetailsDialog(Gtk.Dialog):
    def __init__(
        self,
        parent: Gtk.Window,
        title: str,
        fields: list[tuple[str, str]],
        services_records: list[tuple[str, str, str]] | None = None,
        txt_records: list[tuple[str, str]] | None = None,
        mdns_service_sections: list[dict[str, Any]] | None = None,
        raw_content: str | None = None,
        raw_button_label: str | None = None,
        troubleshooting_records: list[tuple[str, str]] | None = None,
        raw_xml_location: str | None = None,
        icon_mode: str | None = None,
        selected_icon_id: str | None = None,
        icon_choices: list[tuple[str, str, GdkPixbuf.Pixbuf]] | None = None,
        on_apply_icon_settings: Callable[[str, str | None], None] | None = None,
        custom_icons_dir: str | None = None,
        initial_tab: str | None = None,
        has_device_icon_source: bool = True,
        provided_icon_display: str | None = None,
        details_field_rule_map: dict[str, str] | None = None,
        active_field_rules: dict[str, list[str]] | None = None,
        on_add_field_rule: Callable[[str, str], None] | None = None,
        on_remove_field_rule: Callable[[str, str | None], None] | None = None,
        overview: dict[str, str | None] | None = None,
        url_override: str | None = None,
        on_set_url_override: Callable[[str | None], None] | None = None,
        device_commands: list[dict] | None = None,
        on_set_device_commands: Callable[[list[dict]], None] | None = None,
        custom_command: str | None = None,
        on_set_custom_command: Callable[[str | None], None] | None = None,
    ) -> None:
        super().__init__(title=title, transient_for=parent, modal=True)
        prepare_gtk_dialog(self)
        fields = self._filter_unavailable_pairs(fields)
        troubleshooting_records = self._filter_unavailable_pairs(troubleshooting_records)
        txt_records = self._filter_unavailable_pairs(txt_records)
        services_records = self._filter_unavailable_services(services_records)
        mdns_service_sections_list = mdns_service_sections if isinstance(mdns_service_sections, list) else None
        # Simplify UI: merge troubleshooting rows into the main details tab.
        if troubleshooting_records:
            fields.extend(troubleshooting_records)
            troubleshooting_records = None

        close_button = self.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        close_button.connect("clicked", self._on_close_clicked)
        self.set_default_response(Gtk.ResponseType.CLOSE)
        close_button.set_receives_default(True)
        close_button.grab_default()
        close_button.grab_focus()
        self.set_focus(close_button)
        self.set_default_size(720, 520)
        self._raw_content = raw_content
        self._raw_xml_location = raw_xml_location
        self._on_apply_icon_settings = on_apply_icon_settings
        self._selected_icon_id = selected_icon_id
        self._custom_icons_dir = custom_icons_dir
        self._icon_choices = list(icon_choices or [])
        self._selected_icon_label = ""
        self._initial_tab = initial_tab or "details"
        self._has_device_icon_source = bool(has_device_icon_source)
        self._last_icon_mode = "provided"
        self._suppress_icon_mode_events = False
        disp = (provided_icon_display or "").strip()
        self._provided_icon_display: str | None = disp or None
        self._details_field_rule_map = details_field_rule_map if isinstance(details_field_rule_map, dict) else {}
        self._active_field_rules = active_field_rules if isinstance(active_field_rules, dict) else {}
        self._on_add_field_rule = on_add_field_rule
        self._on_remove_field_rule = on_remove_field_rule
        self._overview = overview if isinstance(overview, dict) else None
        self._url_override = (url_override or "").strip() or None
        self._on_set_url_override = on_set_url_override
        self._device_commands: list[dict] = list(device_commands) if isinstance(device_commands, list) else []
        self._on_set_device_commands = on_set_device_commands
        self._custom_command: str | None = (custom_command or "").strip() or None
        self._on_set_custom_command = on_set_custom_command

        area = self.get_content_area()
        area.set_spacing(8)
        area.set_margin_start(6)
        area.set_margin_end(6)
        notebook = Gtk.Notebook()
        notebook.set_hexpand(True)
        notebook.set_vexpand(True)
        area.add(notebook)
        self._notebook = notebook
        self._rules_box: Gtk.Widget | None = None
        self._rules_tab_label: Gtk.Widget | None = None
        self._feedback_timer_id: int | None = None
        self._feedback_revealer = Gtk.Revealer()
        self._feedback_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._feedback_revealer.set_reveal_child(False)
        self._feedback_label = Gtk.Label(label="", xalign=0.0)
        self._feedback_label.get_style_context().add_class("dim-label")
        self._feedback_revealer.add(self._feedback_label)
        area.pack_start(self._feedback_revealer, False, False, 0)
        rules_page_index: int | None = None

        first_tab_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        first_tab_outer.set_margin_start(4)
        first_tab_outer.set_margin_end(4)
        first_tab_outer.set_margin_top(8)
        first_tab_outer.set_margin_bottom(8)

        if self._overview:
            ov = self._overview
            overview_grid = Gtk.Grid(column_spacing=16, row_spacing=10)
            overview_grid.set_margin_start(12)
            overview_grid.set_margin_end(12)
            overview_grid.set_margin_top(4)
            overview_grid.set_margin_bottom(8)
            orow = 0
            for label_key, raw_val in (
                (_("Name"), ov.get("name")),
                (_("IP address"), ov.get("ip")),
                (_("Location"), ov.get("location")),
                (_("Type"), ov.get("type")),
            ):
                val = (raw_val or "").strip() if isinstance(raw_val, str) else ""
                if not val:
                    continue
                kl = Gtk.Label(label=f"{label_key}:", xalign=0.0)
                kl.get_style_context().add_class("dim-label")
                kl.set_halign(Gtk.Align.START)
                overview_grid.attach(kl, 0, orow, 1, 1)
                vl = Gtk.Label(label=val, xalign=0.0)
                vl.set_selectable(True)
                vl.set_line_wrap(True)
                vl.set_halign(Gtk.Align.START)
                overview_grid.attach(vl, 1, orow, 1, 1)
                orow += 1
            ip6 = ov.get("ipv6_link_local")
            ip6_s = (ip6 or "").strip() if isinstance(ip6, str) else ""
            if ip6_s:
                i6l = Gtk.Label(label=f"{_('IPv6 (link-local)')}:", xalign=0.0)
                i6l.get_style_context().add_class("dim-label")
                i6l.set_halign(Gtk.Align.START)
                overview_grid.attach(i6l, 0, orow, 1, 1)
                i6v = Gtk.Label(label=ip6_s, xalign=0.0)
                i6v.set_selectable(True)
                i6v.get_style_context().add_class("dim-label")
                i6v.set_halign(Gtk.Align.START)
                overview_grid.attach(i6v, 1, orow, 1, 1)
                orow += 1
            first_tab_outer.pack_start(overview_grid, False, False, 0)

        if fields:
            details_grid = Gtk.Grid(column_spacing=16, row_spacing=8)
            details_grid.set_margin_start(12)
            details_grid.set_margin_end(12)
            details_grid.set_margin_top(10)
            details_grid.set_margin_bottom(10)
            row = 0
            for key, value in fields:
                key_label = Gtk.Label(label=f"{key}:", xalign=0.0)
                key_label.get_style_context().add_class("dim-label")
                key_label.set_halign(Gtk.Align.START)
                details_grid.attach(key_label, 0, row, 1, 1)
                details_grid.attach(self._create_value_widget(value, self._details_field_rule_map.get(key)), 1, row, 1, 1)
                row += 1
            details_scroll = Gtk.ScrolledWindow()
            details_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            details_scroll.set_hexpand(True)
            details_scroll.set_vexpand(True)
            details_scroll.add(details_grid)
            first_tab_outer.pack_start(details_scroll, True, True, 0)

        details_tab_label = Gtk.Label(label=_("Overview") if self._overview else _("Device details"))
        notebook.append_page(first_tab_outer, details_tab_label)

        if self._on_remove_field_rule is not None or self._on_add_field_rule is not None:
            rules_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            rules_box.set_margin_start(8)
            rules_box.set_margin_end(8)
            rules_box.set_margin_top(8)
            rules_box.set_margin_bottom(8)
            header = Gtk.Label(
                label=_("Per-device field mapping rules. Select rows and remove if needed."),
                xalign=0.0,
            )
            header.set_line_wrap(True)
            rules_box.pack_start(header, False, False, 0)

            self._rules_store = Gtk.ListStore(bool, str, str, str)
            self._rules_tree = Gtk.TreeView(model=self._rules_store)
            toggle = Gtk.CellRendererToggle()
            toggle.connect("toggled", self._on_rules_row_toggled)
            self._rules_tree.append_column(Gtk.TreeViewColumn(_("Select"), toggle, active=0))
            self._rules_tree.append_column(Gtk.TreeViewColumn(_("Target"), Gtk.CellRendererText(), text=1))
            self._rules_tree.append_column(Gtk.TreeViewColumn(_("Source field"), Gtk.CellRendererText(), text=2))
            rules_scroll = Gtk.ScrolledWindow()
            rules_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            rules_scroll.set_hexpand(True)
            rules_scroll.set_vexpand(True)
            rules_scroll.add(self._rules_tree)
            rules_box.pack_start(rules_scroll, True, True, 0)

            controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            remove_selected = Gtk.Button.new_with_label(_("Remove selected"))
            remove_selected.connect("clicked", self._on_remove_selected_rules_clicked)
            controls.pack_start(remove_selected, False, False, 0)
            remove_all = Gtk.Button.new_with_label(_("Remove all"))
            remove_all.connect("clicked", self._on_remove_all_rules_clicked)
            controls.pack_start(remove_all, False, False, 0)
            rules_box.pack_start(controls, False, False, 0)

            self._rules_box = rules_box
            self._rules_tab_label = Gtk.Label(label=_("Rules"))
            self._refresh_rules_store()
            if self._has_any_rules():
                rules_page_index = notebook.append_page(rules_box, self._rules_tab_label)

        if mdns_service_sections_list:
            notebook.append_page(
                self._build_mdns_services_page(mdns_service_sections_list),
                Gtk.Label(label=_("Services")),
            )
        elif services_records is not None:
            services_store = Gtk.ListStore(str, str, str)
            for service_type, target, port in services_records:
                services_store.append([service_type, target, port])
            services_tree = Gtk.TreeView(model=services_store)
            services_tree.append_column(Gtk.TreeViewColumn(_("Service"), Gtk.CellRendererText(), text=0))
            services_tree.append_column(Gtk.TreeViewColumn(_("Target"), Gtk.CellRendererText(), text=1))
            services_tree.append_column(Gtk.TreeViewColumn(_("Port"), Gtk.CellRendererText(), text=2))
            services_scroll = Gtk.ScrolledWindow()
            services_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            services_scroll.set_hexpand(True)
            services_scroll.set_vexpand(True)
            services_scroll.add(services_tree)
            notebook.append_page(services_scroll, Gtk.Label(label=_("Services")))

        if troubleshooting_records:
            trouble_store = Gtk.ListStore(str, str)
            for key, value in troubleshooting_records:
                trouble_store.append([key, value])
            trouble_tree = Gtk.TreeView(model=trouble_store)
            trouble_tree.append_column(Gtk.TreeViewColumn(_("Field"), Gtk.CellRendererText(), text=0))
            trouble_tree.append_column(Gtk.TreeViewColumn(_("Value"), Gtk.CellRendererText(), text=1))
            trouble_scroll = Gtk.ScrolledWindow()
            trouble_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            trouble_scroll.set_hexpand(True)
            trouble_scroll.set_vexpand(True)
            trouble_scroll.add(trouble_tree)
            notebook.append_page(trouble_scroll, Gtk.Label(label=_("Troubleshooting Information")))

        if txt_records and not mdns_service_sections_list:
            txt_store = Gtk.ListStore(str, str)
            for key, value in txt_records:
                txt_store.append([key, value])
            txt_tree = Gtk.TreeView(model=txt_store)
            txt_tree.append_column(Gtk.TreeViewColumn(_("Name"), Gtk.CellRendererText(), text=0))
            txt_tree.append_column(Gtk.TreeViewColumn(_("Record"), Gtk.CellRendererText(), text=1))
            txt_scroll = Gtk.ScrolledWindow()
            txt_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            txt_scroll.set_hexpand(True)
            txt_scroll.set_vexpand(True)
            txt_scroll.add(txt_tree)
            notebook.append_page(txt_scroll, Gtk.Label(label=_("TXT records")))

        if isinstance(self._raw_content, str) and self._raw_content.strip():
            raw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            raw_box.set_margin_start(8)
            raw_box.set_margin_end(8)
            raw_box.set_margin_top(8)
            raw_box.set_margin_bottom(8)

            if self._raw_xml_location:
                location_label = Gtk.Label(label=f"{_('XML location')}: {self._raw_xml_location}", xalign=0.0)
                location_label.set_selectable(True)
                location_label.set_halign(Gtk.Align.START)
                raw_box.pack_start(location_label, False, False, 0)

            copy_button = Gtk.Button.new_with_label(_("Copy to clipboard"))
            copy_button.set_halign(Gtk.Align.START)
            copy_button.connect("clicked", self._on_raw_copy_clicked)
            raw_box.pack_start(copy_button, False, False, 0)

            text_buffer = Gtk.TextBuffer()
            text_buffer.set_text(self._raw_content)
            text_view = Gtk.TextView(buffer=text_buffer)
            text_view.set_editable(False)
            text_view.set_cursor_visible(True)
            text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            text_view.set_monospace(True)

            raw_scroll = Gtk.ScrolledWindow()
            raw_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            raw_scroll.set_hexpand(True)
            raw_scroll.set_vexpand(True)
            raw_scroll.add(text_view)
            raw_box.pack_start(raw_scroll, True, True, 0)
            notebook.append_page(raw_box, Gtk.Label(label=_("Device data")))

        # Options tab: dynamic device commands + custom command + icon settings
        _show_options_tab = (
            self._on_set_device_commands is not None
            or bool(self._device_commands)
            or self._on_set_custom_command is not None
            or on_apply_icon_settings is not None
        )
        options_page_index: int | None = None

        if _show_options_tab:
            options_scroll = Gtk.ScrolledWindow()
            options_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            options_scroll.set_overlay_scrolling(False)
            options_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            options_box.set_margin_start(8)
            options_box.set_margin_end(12)
            options_box.set_margin_top(8)
            options_box.set_margin_bottom(8)

            device_ip = ""
            if isinstance(self._overview, dict):
                device_ip = (self._overview.get("ip") or "").strip().split()[0]

            _SCHEMES = ["http", "https", "smb", "ftp", "ssh", "sftp", "telnet"]
            _SCHEME_PORTS = {"http": 80, "https": 443, "smb": 445, "ftp": 21,
                             "ssh": 22, "sftp": 22, "telnet": 23}
            _SCHEME_LABELS_LOCAL = {"http": "HTTP", "https": "HTTPS", "smb": "SMB",
                                    "ftp": "FTP", "ssh": "SSH", "sftp": "SFTP", "telnet": "Telnet"}
            _MODES = [_("Override"), _("Additional")]

            editable = self._on_set_device_commands is not None

            # --- dynamic rows container ---
            rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

            # working copy of commands (mutated in-place by row callbacks)
            working_cmds: list[dict] = [dict(c) for c in self._device_commands]

            def _commit_all() -> None:
                if self._on_set_device_commands is not None:
                    self._on_set_device_commands([dict(c) for c in working_cmds])

            def _confirm(msg: str) -> bool:
                dlg = Gtk.MessageDialog(
                    transient_for=self, modal=True,
                    message_type=Gtk.MessageType.QUESTION,
                    buttons=Gtk.ButtonsType.NONE,
                    text=msg,
                )
                dlg.add_button(_("No"), Gtk.ResponseType.NO)
                dlg.add_button(_("Yes"), Gtk.ResponseType.YES)
                dlg.set_default_response(Gtk.ResponseType.NO)
                resp = dlg.run()
                dlg.destroy()
                return resp == Gtk.ResponseType.YES

            def _build_row(idx: int) -> Gtk.Box:
                cmd = working_cmds[idx]
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

                # Scheme combo
                scheme_combo = Gtk.ComboBoxText()
                for s in _SCHEMES:
                    scheme_combo.append_text(_SCHEME_LABELS_LOCAL.get(s, s.upper()))
                cur_scheme = str(cmd.get("scheme", "http")).lower()
                scheme_idx = _SCHEMES.index(cur_scheme) if cur_scheme in _SCHEMES else 0
                scheme_combo.set_active(scheme_idx)
                scheme_combo.set_sensitive(editable)

                # Mode combo
                mode_combo = Gtk.ComboBoxText()
                mode_combo.append_text(_("Override"))
                mode_combo.append_text(_("Additional"))
                mode_combo.set_active(0 if cmd.get("mode", "override") == "override" else 1)
                mode_combo.set_sensitive(editable)

                # Label entry
                label_ent = Gtk.Entry()
                label_ent.set_width_chars(10)
                label_ent.set_text(str(cmd.get("label", "")))
                label_ent.set_placeholder_text(_("Label"))
                label_ent.set_sensitive(editable)

                # IP entry
                ip_ent = Gtk.Entry()
                ip_ent.set_width_chars(13)
                ip_ent.set_text(str(cmd.get("ip", "")))
                ip_ent.set_placeholder_text(device_ip or _("IP"))
                ip_ent.set_sensitive(editable)

                # Port entry
                port_ent = Gtk.Entry()
                port_ent.set_width_chars(5)
                cur_port = int(cmd.get("port", 0) or 0)
                port_ent.set_text(str(cur_port) if cur_port else "")
                def_port = _SCHEME_PORTS.get(cur_scheme, 0)
                port_ent.set_placeholder_text(str(def_port) if def_port else "Port")
                port_ent.set_sensitive(editable)

                def _update(_w=None, _ev=None, _i=idx):
                    s = _SCHEMES[scheme_combo.get_active()]
                    m = "override" if mode_combo.get_active() == 0 else "additional"
                    try:
                        p = int(port_ent.get_text().strip() or "0")
                    except ValueError:
                        p = 0
                    ip_val = ip_ent.get_text().strip()
                    ip_valid = _is_valid_ip_or_host(ip_val)
                    ctx = ip_ent.get_style_context()
                    if ip_valid:
                        ctx.remove_class("error")
                    else:
                        ctx.add_class("error")
                    if not ip_valid:
                        return
                    working_cmds[_i] = {
                        "scheme": s, "mode": m,
                        "label": label_ent.get_text().strip(),
                        "ip": ip_val,
                        "port": p,
                    }
                    _commit_all()

                def _update_port_placeholder(_combo, _p_ent=port_ent):
                    s = _SCHEMES[_combo.get_active()]
                    dp = _SCHEME_PORTS.get(s, 0)
                    _p_ent.set_placeholder_text(str(dp) if dp else "Port")

                scheme_combo.connect("changed", _update_port_placeholder)
                scheme_combo.connect("changed", _update)
                mode_combo.connect("changed", _update)
                label_ent.connect("activate", _update)
                label_ent.connect("focus-out-event", _update)
                ip_ent.connect("activate", _update)
                ip_ent.connect("focus-out-event", _update)
                port_ent.connect("activate", _update)
                port_ent.connect("focus-out-event", _update)

                row.pack_start(scheme_combo, False, False, 0)
                row.pack_start(mode_combo, False, False, 0)
                row.pack_start(label_ent, False, False, 0)
                row.pack_start(ip_ent, True, True, 0)
                row.pack_start(Gtk.Label(label=":"), False, False, 0)
                row.pack_start(port_ent, False, False, 0)

                if editable:
                    rm_btn = Gtk.Button()
                    rm_btn.set_relief(Gtk.ReliefStyle.NONE)
                    rm_btn.set_tooltip_text(_("Remove this command"))
                    rm_img = Gtk.Image.new_from_icon_name("list-remove-symbolic", Gtk.IconSize.BUTTON)
                    rm_btn.add(rm_img)

                    def _on_remove(_b, _row=row, _i=idx):
                        if not _confirm(_("Remove this command entry?")):
                            return
                        working_cmds.pop(_i)
                        _row.destroy()
                        _commit_all()
                        # Rebuild remaining rows to keep indices consistent
                        for child in list(rows_box.get_children()):
                            child.destroy()
                        for j in range(len(working_cmds)):
                            rows_box.pack_start(_build_row(j), False, False, 0)
                        rows_box.show_all()

                    rm_btn.connect("clicked", _on_remove)
                    row.pack_start(rm_btn, False, False, 0)

                return row

            # Populate existing rows
            for i in range(len(working_cmds)):
                rows_box.pack_start(_build_row(i), False, False, 0)

            options_box.pack_start(rows_box, False, False, 0)

            if editable:
                add_btn = Gtk.Button.new_with_label(_("+ Add command"))
                add_btn.set_halign(Gtk.Align.START)

                def _on_add(_b):
                    working_cmds.append({"scheme": "http", "mode": "override",
                                         "label": "", "ip": "", "port": 0})
                    rows_box.pack_start(_build_row(len(working_cmds) - 1), False, False, 0)
                    rows_box.show_all()

                add_btn.connect("clicked", _on_add)
                options_box.pack_start(add_btn, False, False, 4)

            # Custom command per device
            _show_custom_cmd = self._on_set_custom_command is not None or self._custom_command is not None
            if _show_custom_cmd:
                cmd_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                cmd_sep.set_margin_top(6)
                cmd_sep.set_margin_bottom(4)
                options_box.pack_start(cmd_sep, False, False, 0)

                cmd_hdr = Gtk.Label(label=_("Custom command"), xalign=0.0)
                cmd_hdr.get_style_context().add_class("dim-label")
                options_box.pack_start(cmd_hdr, False, False, 0)

                cmd_ent = Gtk.Entry()
                cmd_ent.set_hexpand(True)
                cmd_ent.set_text(self._custom_command or "")
                cmd_ent.set_placeholder_text(_("e.g. x-terminal-emulator -e ssh -p {port} {ip}"))
                cmd_ent.set_margin_start(4)
                cmd_ent.set_margin_end(4)
                if self._on_set_custom_command is None:
                    cmd_ent.set_sensitive(False)

                _cmd_cb = self._on_set_custom_command

                def _commit_cmd(_w=None, _ev=None, _e=cmd_ent, _cb=_cmd_cb):
                    if _cb is None:
                        return
                    val = _e.get_text().strip()
                    _cb(val if val else None)

                cmd_ent.connect("activate", _commit_cmd)
                cmd_ent.connect("focus-out-event", _commit_cmd)

                cmd_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                cmd_row.pack_start(cmd_ent, True, True, 0)
                if self._on_set_custom_command is not None:
                    def _on_clear_cmd(_b):
                        if not _confirm(_("Clear the per-device custom command?")):
                            return
                        cmd_ent.set_text("")
                        if _cmd_cb is not None:
                            _cmd_cb(None)
                    clear_btn = Gtk.Button.new_with_label(_("Clear"))
                    clear_btn.set_tooltip_text(_("Remove per-device override (use global command)"))
                    clear_btn.connect("clicked", _on_clear_cmd)
                    cmd_row.pack_start(clear_btn, False, False, 0)

                hint = Gtk.Label(xalign=0.0)
                hint.set_markup("<small><i>{ip}  {port}  {name}  {type}  {category}  {url}</i></small>")
                hint.get_style_context().add_class("dim-label")
                hint.set_margin_start(4)

                options_box.pack_start(cmd_row, False, False, 2)
                options_box.pack_start(hint, False, False, 0)

            if on_apply_icon_settings is not None:
                if self._device_commands or _show_custom_cmd:
                    icon_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                    icon_sep.set_margin_top(8)
                    icon_sep.set_margin_bottom(4)
                    options_box.pack_start(icon_sep, False, False, 0)

                mode_label = Gtk.Label(label=_("Use icon from:"), xalign=0.0)
                options_box.pack_start(mode_label, False, False, 0)
                self._icon_mode_system_item = Gtk.RadioButton.new_with_label_from_widget(None, _("System"))
                self._icon_mode_provided_item = Gtk.RadioButton.new_with_label_from_widget(
                    self._icon_mode_system_item, _("From device")
                )
                self._icon_mode_custom_item = Gtk.RadioButton.new_with_label_from_widget(
                    self._icon_mode_system_item, _("Custom")
                )
                options_box.pack_start(self._icon_mode_system_item, False, False, 0)
                if self._has_device_icon_source:
                    options_box.pack_start(self._icon_mode_provided_item, False, False, 0)
                options_box.pack_start(self._icon_mode_custom_item, False, False, 0)

                self._selected_icon_label_widget = Gtk.Label(label="", xalign=0.0)
                self._selected_icon_label_widget.set_line_wrap(True)
                self._selected_icon_label_widget.set_selectable(True)
                options_box.pack_start(self._selected_icon_label_widget, False, False, 0)

                open_folder_button = Gtk.Button.new_with_label(_("Open custom icons folder"))
                open_folder_button.set_halign(Gtk.Align.START)
                open_folder_button.connect("clicked", self._on_open_custom_icons_folder_clicked)
                options_box.pack_start(open_folder_button, False, False, 0)

                normalized_mode = icon_mode if icon_mode in {"system", "provided", "custom"} else "provided"
                if normalized_mode == "provided" and not self._has_device_icon_source:
                    normalized_mode = "system"
                self._last_icon_mode = normalized_mode

                self._suppress_icon_mode_events = True
                if normalized_mode == "system":
                    self._icon_mode_system_item.set_active(True)
                elif normalized_mode == "custom":
                    self._icon_mode_custom_item.set_active(True)
                else:
                    self._icon_mode_provided_item.set_active(True)
                self._suppress_icon_mode_events = False
                self._select_custom_icon(selected_icon_id)
                self._icon_mode_system_item.connect("toggled", self._on_icon_mode_toggled, "system")
                self._icon_mode_provided_item.connect("toggled", self._on_icon_mode_toggled, "provided")
                self._icon_mode_custom_item.connect("toggled", self._on_custom_mode_toggled)

            options_scroll.add(options_box)
            options_page_index = notebook.append_page(options_scroll, Gtk.Label(label=_("Options")))

        self.connect("response", self._on_response)
        self.show_all()
        if self._initial_tab in ("appearance", "icon", "options") and options_page_index is not None:
            notebook.set_current_page(options_page_index)
        if self._initial_tab == "rules" and rules_page_index is not None:
            notebook.set_current_page(rules_page_index)

    def _on_close_clicked(self, _button: Gtk.Button) -> None:
        _LOG.debug("Device details dialog close button clicked")

    def _on_response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        _LOG.debug("Device details dialog response=%s", response_id)
        if self._feedback_timer_id is not None:
            GLib.source_remove(self._feedback_timer_id)
            self._feedback_timer_id = None
        return

    def _on_raw_copy_clicked(self, _button: Gtk.Button) -> None:
        if not isinstance(self._raw_content, str):
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(self._raw_content, -1)
        clipboard.store()
        _LOG.debug("Raw XML copied to clipboard")

    def _current_icon_mode(self) -> str:
        if self._icon_mode_system_item.get_active():
            return "system"
        if self._icon_mode_custom_item.get_active():
            return "custom"
        if self._has_device_icon_source and self._icon_mode_provided_item.get_active():
            return "provided"
        return "system"

    def _refresh_icon_detail_line(self) -> None:
        if not hasattr(self, "_selected_icon_label_widget"):
            return
        w = self._selected_icon_label_widget
        mode = self._current_icon_mode()
        if mode == "system":
            w.hide()
            return
        w.show()
        if mode == "provided":
            detail = self._provided_icon_display or _("unavailable")
            w.set_text(f"{_('Image from device')}: {detail}")
            return
        label = self._selected_icon_label or _("none")
        w.set_text(f"{_('Selected icon')}: {label}")

    def _select_custom_icon(self, icon_name: str | None) -> None:
        if not icon_name:
            self._selected_icon_id = None
            self._selected_icon_label = _("none")
        else:
            self._selected_icon_id = icon_name
            for icon_id, icon_label, _pix in self._icon_choices:
                if icon_id == icon_name:
                    self._selected_icon_label = icon_label
                    break
            else:
                self._selected_icon_label = icon_name
        self._refresh_icon_detail_line()

    def _on_icon_mode_toggled(self, button: Gtk.RadioButton, mode: str) -> None:
        if self._on_apply_icon_settings is None or self._suppress_icon_mode_events:
            return
        if not button.get_active():
            return
        if mode == "custom":
            return
        if mode == "provided" and not self._has_device_icon_source:
            self._restore_previous_icon_mode()
            return
        self._on_apply_icon_settings(mode, None)
        self._last_icon_mode = mode
        self._refresh_icon_detail_line()

    def _on_custom_mode_toggled(self, button: Gtk.RadioButton) -> None:
        if self._on_apply_icon_settings is None or self._suppress_icon_mode_events:
            return
        if not button.get_active():
            return
        selected_id = self._open_icon_picker_dialog()
        if not selected_id:
            self._restore_previous_icon_mode()
            return
        self._select_custom_icon(selected_id)
        self._on_apply_icon_settings("custom", selected_id)
        self._last_icon_mode = "custom"

    def _restore_previous_icon_mode(self) -> None:
        self._suppress_icon_mode_events = True
        if self._last_icon_mode == "custom":
            self._icon_mode_custom_item.set_active(True)
        elif self._last_icon_mode == "provided" and self._has_device_icon_source:
            self._icon_mode_provided_item.set_active(True)
        else:
            self._icon_mode_system_item.set_active(True)
        self._suppress_icon_mode_events = False
        self._refresh_icon_detail_line()

    def _open_icon_picker_dialog(self) -> str | None:
        dialog = Gtk.Dialog(title=_("Choose icon"), transient_for=self, modal=True)
        prepare_gtk_dialog(dialog)
        dialog.set_default_size(520, 380)
        area = dialog.get_content_area()
        area.set_border_width(8)
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        flow.set_max_children_per_line(6)
        flow.set_row_spacing(8)
        flow.set_column_spacing(8)
        flow.set_homogeneous(False)
        flow.set_valign(Gtk.Align.START)

        for icon_id, _icon_label, icon_pixbuf in self._icon_choices:
            item_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            item_box.set_margin_start(10)
            item_box.set_margin_end(10)
            item_box.set_margin_top(10)
            item_box.set_margin_bottom(10)
            image = Gtk.Image.new_from_pixbuf(icon_pixbuf)
            image.set_halign(Gtk.Align.CENTER)
            image.set_valign(Gtk.Align.CENTER)
            item_box.pack_start(image, False, False, 0)
            item_box.set_halign(Gtk.Align.CENTER)
            item_box.set_valign(Gtk.Align.CENTER)
            child = Gtk.FlowBoxChild()
            child.icon_id = icon_id
            child.set_size_request(84, 84)
            child.set_hexpand(False)
            child.set_vexpand(False)
            child.set_halign(Gtk.Align.CENTER)
            child.set_valign(Gtk.Align.START)
            child.add(item_box)
            flow.add(child)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.add(flow)
        area.pack_start(scroll, True, True, 0)
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Select"), Gtk.ResponseType.OK)
        dialog.show_all()

        # Preselect current icon if available.
        for child in flow.get_children():
            if getattr(child, "icon_id", None) == self._selected_icon_id:
                flow.select_child(child)
                break
        flow.connect("child-activated", self._on_icon_picker_child_activated, dialog)

        response = dialog.run()
        selected_result = None
        if response == Gtk.ResponseType.OK:
            selected_children = flow.get_selected_children()
            if selected_children:
                selected_id = getattr(selected_children[0], "icon_id", None)
                if selected_id:
                    selected_result = str(selected_id)
        dialog.destroy()
        return selected_result

    def _on_icon_picker_child_activated(self, _flow: Gtk.FlowBox, _child: Gtk.FlowBoxChild, dialog: Gtk.Dialog) -> None:
        dialog.response(Gtk.ResponseType.OK)

    def _on_open_custom_icons_folder_clicked(self, _button: Gtk.Button) -> None:
        folder = self._custom_icons_dir
        if not folder:
            return
        try:
            path = Path(folder).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            _LOG.debug("Failed to open custom icons folder: %s", folder, exc_info=True)

    def _create_value_widget(self, value: str, field_path: str | None = None) -> Gtk.Widget:
        text = value.strip() if isinstance(value, str) else str(value)
        popup_handler = None
        if isinstance(field_path, str) and field_path.strip():
            path_norm = field_path.strip()

            def _on_popup(widget, event) -> bool:
                if event.type != Gdk.EventType.BUTTON_PRESS or event.button != 3:
                    return False
                self._show_field_rule_menu(path_norm, event.button, event.time)
                return True

            popup_handler = _on_popup
        if (
            "\n" not in text
            and (text.startswith("http://") or text.startswith("https://"))
        ):
            link = Gtk.LinkButton.new_with_label(text, text)
            link.set_halign(Gtk.Align.START)
            if popup_handler is not None:
                link.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
                link.connect("button-press-event", popup_handler)
            return link
        label = Gtk.Label(label=text, xalign=0.0)
        label.set_selectable(True)
        label.set_line_wrap(True)
        label.set_halign(Gtk.Align.START)
        if popup_handler is not None:
            label.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            label.connect("button-press-event", popup_handler)
        return label

    def _field_rule_is_active(self, target: str, field_path: str) -> bool:
        bucket = self._active_field_rules.get(target)
        return isinstance(bucket, list) and field_path in bucket

    def _show_field_rule_menu(self, field_path: str, button: int, event_time: int) -> None:
        if self._on_add_field_rule is None and self._on_remove_field_rule is None:
            return
        menu = Gtk.Menu()
        labels = {
            "name": _("Friendly name"),
            "location": _("Location"),
            "information": _("Information"),
        }
        for target in ("name", "location", "information"):
            active = self._field_rule_is_active(target, field_path)
            if self._on_add_field_rule is not None:
                add_item = Gtk.MenuItem.new_with_label(_("Use as {target}").format(target=labels[target]))
                add_item.set_sensitive(not active)
                add_item.connect("activate", self._on_field_rule_add_activate, target, field_path)
                menu.append(add_item)
            if self._on_remove_field_rule is not None and active:
                remove_item = Gtk.MenuItem.new_with_label(_("Remove {target} rule").format(target=labels[target]))
                remove_item.connect("activate", self._on_field_rule_remove_activate, target, field_path)
                menu.append(remove_item)
        menu.show_all()
        menu.popup(None, None, None, None, button, event_time)

    def _on_field_rule_add_activate(self, _item: Gtk.MenuItem, target: str, field_path: str) -> None:
        if self._on_add_field_rule is None:
            return
        self._on_add_field_rule(target, field_path)
        bucket = self._active_field_rules.get(target)
        if not isinstance(bucket, list):
            bucket = []
        if field_path not in bucket:
            bucket.append(field_path)
        self._active_field_rules[target] = bucket
        self._refresh_rules_store()
        self._show_feedback_message(_("Rule saved"))

    def _on_field_rule_remove_activate(self, _item: Gtk.MenuItem, target: str, field_path: str) -> None:
        if self._on_remove_field_rule is None:
            return
        self._on_remove_field_rule(target, field_path)
        bucket = self._active_field_rules.get(target)
        if isinstance(bucket, list):
            self._active_field_rules[target] = [x for x in bucket if x != field_path]
        self._refresh_rules_store()
        self._show_feedback_message(_("Rule removed"))

    def _refresh_rules_store(self) -> None:
        if not hasattr(self, "_rules_store"):
            return
        self._rules_store.clear()
        for target in ("name", "location", "information"):
            bucket = self._active_field_rules.get(target)
            if not isinstance(bucket, list):
                continue
            for field_path in bucket:
                if isinstance(field_path, str) and field_path.strip():
                    raw = field_path.strip()
                    self._rules_store.append([False, target, self._pretty_field_path(raw), raw])
        self._sync_rules_tab_visibility()

    def _on_rules_row_toggled(self, _renderer: Gtk.CellRendererToggle, path_str: str) -> None:
        if not hasattr(self, "_rules_store"):
            return
        path = Gtk.TreePath.new_from_string(path_str)
        tree_iter = self._rules_store.get_iter(path)
        current = bool(self._rules_store.get_value(tree_iter, 0))
        self._rules_store.set_value(tree_iter, 0, not current)

    def _on_remove_selected_rules_clicked(self, _button: Gtk.Button) -> None:
        if self._on_remove_field_rule is None or not hasattr(self, "_rules_store"):
            return
        to_remove: list[tuple[str, str]] = []
        tree_iter = self._rules_store.get_iter_first()
        while tree_iter is not None:
            selected = bool(self._rules_store.get_value(tree_iter, 0))
            if selected:
                target = str(self._rules_store.get_value(tree_iter, 1))
                field_path = str(self._rules_store.get_value(tree_iter, 3))
                to_remove.append((target, field_path))
            tree_iter = self._rules_store.iter_next(tree_iter)
        if not to_remove:
            return
        for target, field_path in to_remove:
            self._on_remove_field_rule(target, field_path)
            bucket = self._active_field_rules.get(target)
            if isinstance(bucket, list):
                self._active_field_rules[target] = [x for x in bucket if x != field_path]
        self._refresh_rules_store()
        self._show_feedback_message(_("Selected rules removed"))

    def _on_remove_all_rules_clicked(self, _button: Gtk.Button) -> None:
        if self._on_remove_field_rule is None:
            return
        for target in ("name", "location", "information"):
            bucket = self._active_field_rules.get(target)
            if not isinstance(bucket, list) or not bucket:
                continue
            self._on_remove_field_rule(target, None)
            self._active_field_rules[target] = []
        self._refresh_rules_store()
        self._show_feedback_message(_("All rules removed"))

    def _has_any_rules(self) -> bool:
        for target in ("name", "location", "information"):
            bucket = self._active_field_rules.get(target)
            if isinstance(bucket, list) and any(isinstance(v, str) and v.strip() for v in bucket):
                return True
        return False

    def _sync_rules_tab_visibility(self) -> None:
        if not hasattr(self, "_notebook"):
            return
        if self._rules_box is None or self._rules_tab_label is None:
            return
        page_idx = self._notebook.page_num(self._rules_box)
        if self._has_any_rules():
            if page_idx < 0:
                self._notebook.append_page(self._rules_box, self._rules_tab_label)
        elif page_idx >= 0:
            self._notebook.remove_page(page_idx)

    def _show_feedback_message(self, message: str) -> None:
        if not isinstance(message, str) or not message.strip():
            return
        if self._feedback_timer_id is not None:
            GLib.source_remove(self._feedback_timer_id)
            self._feedback_timer_id = None
        self._feedback_label.set_text(message.strip())
        self._feedback_revealer.set_reveal_child(True)

        def _hide_feedback() -> bool:
            self._feedback_revealer.set_reveal_child(False)
            self._feedback_timer_id = None
            return False

        self._feedback_timer_id = GLib.timeout_add(1800, _hide_feedback)

    def _pretty_field_path(self, raw: str) -> str:
        text = str(raw).strip()
        if ":" not in text:
            return text
        prefix, rest = text.split(":", 1)
        key = rest.strip()
        if prefix == "txt":
            return f"mDNS TXT: {key}"
        if prefix == "xml":
            return f"SSDP XML: {key}"
        if prefix == "meta":
            return f"Metadata: {key}"
        return text

    def _filter_mdns_txt_pairs(self, pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Drop unusable decoded rows while keeping TXT flags (empty value ok)."""

        cleaned: list[tuple[str, str]] = []
        for key, value in pairs:
            key_str = str(key).strip() if isinstance(key, str) else str(key)
            vs = value.strip().lower() if isinstance(value, str) else str(value).strip().lower()
            if vs == "unavailable":
                continue
            if not key_str:
                continue
            cleaned.append((key_str, value if isinstance(value, str) else str(value)))
        return cleaned

    def _build_mdns_services_page(self, sections: list[dict[str, Any]]) -> Gtk.ScrolledWindow:
        outer_scroll = Gtk.ScrolledWindow()
        outer_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        outer_scroll.set_hexpand(True)
        outer_scroll.set_vexpand(True)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)

        for raw in sections:
            if not isinstance(raw, dict):
                continue
            heading = raw.get("heading") or raw.get("service_type") or _("Service")
            service_type = str(raw.get("service_type", "")).strip() or _("unavailable")
            target = str(raw.get("target", "")).strip() or _("unavailable")
            port = str(raw.get("port", "")).strip()

            row_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            row_ctx = row_outer.get_style_context()
            row_ctx.add_class("nn-mdns-service-block")

            header_btn = Gtk.Button()
            header_btn.set_relief(Gtk.ReliefStyle.NONE)
            header_btn.get_style_context().add_class("flat")
            header_btn.get_style_context().add_class("nn-mdns-service-header")

            arrow_lbl = Gtk.Label(label="▸", xalign=0.0)
            arrow_lbl.get_style_context().add_class("dim-label")
            title_lbl = Gtk.Label(label=str(heading), xalign=0.0)
            title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            title_lbl.set_hexpand(True)
            header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            header_box.pack_start(arrow_lbl, False, False, 0)
            header_box.pack_start(title_lbl, True, True, 0)
            header_btn.add(header_box)

            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            inner.set_margin_start(8)
            inner.get_style_context().add_class("nn-mdns-service-body")

            meta_grid = Gtk.Grid(column_spacing=10, row_spacing=4)
            row_i = 0
            lbl_st = Gtk.Label(label=f"{_('Service type')}:", xalign=0.0)
            lbl_st.get_style_context().add_class("dim-label")
            val_st = Gtk.Label(label=service_type, xalign=0.0)
            val_st.set_selectable(True)
            meta_grid.attach(lbl_st, 0, row_i, 1, 1)
            meta_grid.attach(val_st, 1, row_i, 1, 1)
            row_i += 1

            lbl_tgt = Gtk.Label(label=f"{_('Target')}:", xalign=0.0)
            lbl_tgt.get_style_context().add_class("dim-label")
            val_tgt = Gtk.Label(label=target, xalign=0.0)
            val_tgt.set_selectable(True)
            meta_grid.attach(lbl_tgt, 0, row_i, 1, 1)
            meta_grid.attach(val_tgt, 1, row_i, 1, 1)
            row_i += 1

            lbl_pt = Gtk.Label(label=f"{_('Port')}:", xalign=0.0)
            lbl_pt.get_style_context().add_class("dim-label")
            val_pt = Gtk.Label(label=port or "—", xalign=0.0)
            val_pt.set_selectable(True)
            meta_grid.attach(lbl_pt, 0, row_i, 1, 1)
            meta_grid.attach(val_pt, 1, row_i, 1, 1)

            inner.pack_start(meta_grid, False, False, 0)

            lbl_kv = Gtk.Label(label=f"{_('TXT records')}:", xalign=0.0)
            lbl_kv.get_style_context().add_class("dim-label")
            lbl_kv.set_margin_top(8)
            inner.pack_start(lbl_kv, False, False, 0)

            pairs_f: list[tuple[str, str]] = []
            pairs_raw = raw.get("txt_records")
            if isinstance(pairs_raw, list):
                for row in pairs_raw:
                    if isinstance(row, (list, tuple)) and len(row) >= 2:
                        pairs_f.append((str(row[0]), "" if row[1] is None else str(row[1])))
            filtered = self._filter_mdns_txt_pairs(pairs_f)
            if not filtered:
                none_lbl = Gtk.Label(label=_("No TXT records"), xalign=0.0)
                none_lbl.get_style_context().add_class("dim-label")
                inner.pack_start(none_lbl, False, False, 0)
            else:
                txt_store = Gtk.ListStore(str, str)
                for tk, tv in filtered:
                    txt_store.append([tk, tv])
                txt_tree = Gtk.TreeView(model=txt_store)
                txt_tree.append_column(Gtk.TreeViewColumn(_("Name"), Gtk.CellRendererText(), text=0))
                txt_tree.append_column(Gtk.TreeViewColumn(_("Value"), Gtk.CellRendererText(), text=1))
                txt_tree.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
                txt_tree.connect("button-press-event", self._on_mdns_txt_tree_button_press)
                # No nested ScrolledWindow: full TXT height so only the tab's outer scrollbar scrolls.
                row_h = 26
                tree_h = len(filtered) * row_h + 40
                txt_tree.set_size_request(-1, tree_h)
                inner.pack_start(txt_tree, False, False, 0)

            revealer = Gtk.Revealer()
            revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
            revealer.set_reveal_child(False)
            revealer.add(inner)

            def _toggle_header(_b: Gtk.Button, ar=arrow_lbl, rv=revealer) -> None:
                open_ = not rv.get_reveal_child()
                rv.set_reveal_child(open_)
                ar.set_label("▼" if open_ else "▸")

            header_btn.connect("clicked", _toggle_header)

            row_outer.pack_start(header_btn, False, False, 0)
            row_outer.pack_start(revealer, False, False, 0)
            vbox.pack_start(row_outer, False, False, 0)

        outer_scroll.add(vbox)
        return outer_scroll

    def _on_mdns_txt_tree_button_press(self, tree: Gtk.TreeView, event) -> bool:
        if event.type != Gdk.EventType.BUTTON_PRESS or event.button != 3:
            return False
        if self._on_add_field_rule is None and self._on_remove_field_rule is None:
            return False
        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        if hit is None:
            return False
        path, _col, _cx, _cy = hit
        tree.grab_focus()
        tree.set_cursor(path, None, False)
        model = tree.get_model()
        tree_iter = model.get_iter(path)
        key = model.get_value(tree_iter, 0)
        if not isinstance(key, str) or not key.strip():
            return False
        self._show_field_rule_menu(f"txt:{key.strip()}", event.button, event.time)
        return True

    def _filter_unavailable_pairs(self, records: list[tuple[str, str]] | None) -> list[tuple[str, str]]:
        if not records:
            return []
        # Keep diagnostic rows visible even when discovery did not provide a value (explain absence).
        always_show = frozenset({"mac address", "serial number", "unique identifier"})
        cleaned: list[tuple[str, str]] = []
        for key, value in records:
            text = value.strip() if isinstance(value, str) else str(value).strip()
            lk = str(key).strip().lower()
            if not text or text.lower() == "unavailable":
                if lk in always_show:
                    cleaned.append((key, _("unavailable")))
                continue
            cleaned.append((key, text))
        return cleaned

    def _filter_unavailable_services(
        self, records: list[tuple[str, str, str]] | None
    ) -> list[tuple[str, str, str]] | None:
        if not records:
            return None
        cleaned: list[tuple[str, str, str]] = []
        for service, target, port in records:
            target_txt = target.strip() if isinstance(target, str) else str(target).strip()
            port_txt = port.strip() if isinstance(port, str) else str(port).strip()
            if (not target_txt or target_txt.lower() == "unavailable") and (
                not port_txt or port_txt.lower() == "unavailable"
            ):
                continue
            cleaned.append((service, target_txt or "unavailable", port_txt or "unavailable"))
        return cleaned or None
