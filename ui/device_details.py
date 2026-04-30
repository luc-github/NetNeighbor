"""Read-only protocol details dialog."""

import gi
import logging

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

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

        area = self.get_content_area()
        area.set_spacing(8)
        area.set_margin_start(6)
        area.set_margin_end(6)
        notebook = Gtk.Notebook()
        notebook.set_hexpand(True)
        notebook.set_vexpand(True)
        area.add(notebook)

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

        self.connect("response", self._on_response)
        self.show_all()

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
