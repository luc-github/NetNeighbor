"""Read-only protocol details dialog."""

import gi
import logging
from collections.abc import Callable
from pathlib import Path
import subprocess

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk

_LOG = logging.getLogger(__name__)


class DeviceDetailsDialog(Gtk.Dialog):
    def __init__(
        self,
        parent: Gtk.Window,
        title: str,
        fields: list[tuple[str, str]],
        services_records: list[tuple[str, str, str]] | None = None,
        txt_records: list[tuple[str, str]] | None = None,
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
    ) -> None:
        super().__init__(title=title, transient_for=parent, modal=True)
        fields = self._filter_unavailable_pairs(fields)
        troubleshooting_records = self._filter_unavailable_pairs(troubleshooting_records)
        txt_records = self._filter_unavailable_pairs(txt_records)
        services_records = self._filter_unavailable_services(services_records)
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

        area = self.get_content_area()
        area.set_spacing(8)
        area.set_margin_start(6)
        area.set_margin_end(6)
        notebook = Gtk.Notebook()
        notebook.set_hexpand(True)
        notebook.set_vexpand(True)
        area.add(notebook)
        appearance_page_index: int | None = None

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
            details_grid.attach(self._create_value_widget(value), 1, row, 1, 1)
            row += 1
        details_scroll = Gtk.ScrolledWindow()
        details_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        details_scroll.set_hexpand(True)
        details_scroll.set_vexpand(True)
        details_scroll.add(details_grid)
        notebook.append_page(details_scroll, Gtk.Label(label=_("Device details")))

        if services_records is not None:
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

        if txt_records:
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

        if on_apply_icon_settings is not None:
            appearance_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            appearance_box.set_margin_start(8)
            appearance_box.set_margin_end(8)
            appearance_box.set_margin_top(8)
            appearance_box.set_margin_bottom(8)

            mode_label = Gtk.Label(label=_("Use icon from:"), xalign=0.0)
            appearance_box.pack_start(mode_label, False, False, 0)
            self._icon_mode_system_item = Gtk.RadioButton.new_with_label_from_widget(None, _("System"))
            self._icon_mode_provided_item = Gtk.RadioButton.new_with_label_from_widget(
                self._icon_mode_system_item, _("Device (SSDP)")
            )
            self._icon_mode_custom_item = Gtk.RadioButton.new_with_label_from_widget(
                self._icon_mode_system_item, _("Custom")
            )
            appearance_box.pack_start(self._icon_mode_system_item, False, False, 0)
            if self._has_device_icon_source:
                appearance_box.pack_start(self._icon_mode_provided_item, False, False, 0)
            appearance_box.pack_start(self._icon_mode_custom_item, False, False, 0)

            self._selected_icon_label_widget = Gtk.Label(label=_("Selected icon: none"), xalign=0.0)
            self._selected_icon_label_widget.set_line_wrap(True)
            appearance_box.pack_start(self._selected_icon_label_widget, False, False, 0)

            open_folder_button = Gtk.Button.new_with_label(_("Open custom icons folder"))
            open_folder_button.set_halign(Gtk.Align.START)
            open_folder_button.connect("clicked", self._on_open_custom_icons_folder_clicked)
            appearance_box.pack_start(open_folder_button, False, False, 0)

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
            self._icon_mode_custom_item.connect("clicked", self._on_custom_mode_clicked)
            appearance_page_index = notebook.append_page(appearance_box, Gtk.Label(label=_("Appearance")))
        self.connect("response", self._on_response)
        self.show_all()
        if self._initial_tab == "appearance" and appearance_page_index is not None:
            notebook.set_current_page(appearance_page_index)

    def _on_close_clicked(self, _button: Gtk.Button) -> None:
        _LOG.debug("Device details dialog close button clicked")

    def _on_response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        _LOG.debug("Device details dialog response=%s", response_id)
        return

    def _on_raw_copy_clicked(self, _button: Gtk.Button) -> None:
        if not isinstance(self._raw_content, str):
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(self._raw_content, -1)
        clipboard.store()
        _LOG.debug("Raw XML copied to clipboard")

    def _select_custom_icon(self, icon_name: str | None) -> None:
        if not icon_name:
            self._selected_icon_id = None
            self._selected_icon_label = _("none")
            if hasattr(self, "_selected_icon_label_widget"):
                self._selected_icon_label_widget.set_text(f"{_('Selected icon')}: {self._selected_icon_label}")
            return
        self._selected_icon_id = icon_name
        for icon_id, icon_label, _pix in self._icon_choices:
            if icon_id == icon_name:
                self._selected_icon_label = icon_label
                break
        else:
            self._selected_icon_label = icon_name
        if hasattr(self, "_selected_icon_label_widget"):
            self._selected_icon_label_widget.set_text(f"{_('Selected icon')}: {self._selected_icon_label}")

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

    def _on_custom_mode_clicked(self, button: Gtk.RadioButton) -> None:
        if self._on_apply_icon_settings is None or self._suppress_icon_mode_events:
            return
        if not button.get_active():
            self._suppress_icon_mode_events = True
            button.set_active(True)
            self._suppress_icon_mode_events = False
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

    def _open_icon_picker_dialog(self) -> str | None:
        dialog = Gtk.Dialog(title=_("Choose icon"), transient_for=self, modal=True)
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

    def _create_value_widget(self, value: str) -> Gtk.Widget:
        text = value.strip() if isinstance(value, str) else str(value)
        if text.startswith("http://") or text.startswith("https://"):
            link = Gtk.LinkButton.new_with_label(text, text)
            link.set_halign(Gtk.Align.START)
            return link
        label = Gtk.Label(label=text, xalign=0.0)
        label.set_selectable(True)
        label.set_line_wrap(True)
        label.set_halign(Gtk.Align.START)
        return label

    def _filter_unavailable_pairs(self, records: list[tuple[str, str]] | None) -> list[tuple[str, str]]:
        if not records:
            return []
        cleaned: list[tuple[str, str]] = []
        for key, value in records:
            text = value.strip() if isinstance(value, str) else str(value).strip()
            if not text or text.lower() == "unavailable":
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
