"""Read-only protocol details dialog."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


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
        self.add_button(_("Close"), Gtk.ResponseType.CLOSE)
        if raw_content and raw_button_label:
            self.add_button(raw_button_label, Gtk.ResponseType.APPLY)
        self.set_default_size(720, 520)
        self._raw_content = raw_content
        self._raw_xml_location = raw_xml_location

        area = self.get_content_area()
        area.set_spacing(8)
        area.set_margin_start(6)
        area.set_margin_end(6)

        fields_frame = Gtk.Frame(label=_("Device details"))
        fields_frame.set_vexpand(True)
        fields_frame.set_hexpand(True)
        fields_frame.set_margin_start(6)
        fields_frame.set_margin_end(6)
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

        fields_scroll = Gtk.ScrolledWindow()
        fields_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        fields_scroll.set_vexpand(True)
        fields_scroll.set_hexpand(True)
        fields_scroll.add(details_grid)
        fields_frame.add(fields_scroll)
        area.add(fields_frame)

        if services_records is not None:
            services_frame = Gtk.Frame(label=_("Services"))
            services_frame.set_margin_start(6)
            services_frame.set_margin_end(6)
            services_frame.set_vexpand(False)

            services_store = Gtk.ListStore(str, str, str)
            for service_type, target, port in services_records:
                services_store.append([service_type, target, port])

            services_tree = Gtk.TreeView(model=services_store)
            service_col = Gtk.TreeViewColumn(_("Service"), Gtk.CellRendererText(), text=0)
            target_col = Gtk.TreeViewColumn(_("Target"), Gtk.CellRendererText(), text=1)
            port_col = Gtk.TreeViewColumn(_("Port"), Gtk.CellRendererText(), text=2)
            service_col.set_resizable(True)
            target_col.set_resizable(True)
            port_col.set_resizable(True)
            services_tree.append_column(service_col)
            services_tree.append_column(target_col)
            services_tree.append_column(port_col)

            services_scroll = Gtk.ScrolledWindow()
            services_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            services_scroll.set_min_content_height(120)
            services_scroll.add(services_tree)
            services_frame.add(services_scroll)
            area.add(services_frame)

        if troubleshooting_records is not None:
            trouble_frame = Gtk.Frame(label=_("Troubleshooting Information"))
            trouble_frame.set_margin_start(6)
            trouble_frame.set_margin_end(6)
            trouble_frame.set_vexpand(False)

            trouble_store = Gtk.ListStore(str, str)
            for key, value in troubleshooting_records:
                trouble_store.append([key, value])

            trouble_tree = Gtk.TreeView(model=trouble_store)
            trouble_key_col = Gtk.TreeViewColumn(_("Field"), Gtk.CellRendererText(), text=0)
            trouble_val_col = Gtk.TreeViewColumn(_("Value"), Gtk.CellRendererText(), text=1)
            trouble_key_col.set_resizable(True)
            trouble_val_col.set_resizable(True)
            trouble_tree.append_column(trouble_key_col)
            trouble_tree.append_column(trouble_val_col)

            trouble_scroll = Gtk.ScrolledWindow()
            trouble_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            trouble_scroll.set_min_content_height(120)
            trouble_scroll.add(trouble_tree)

            trouble_frame.add(trouble_scroll)
            area.add(trouble_frame)

        if txt_records is not None:
            txt_frame = Gtk.Frame(label=_("TXT records"))
            txt_frame.set_margin_start(6)
            txt_frame.set_margin_end(6)
            txt_store = Gtk.ListStore(str, str)
            for key, value in txt_records:
                txt_store.append([key, value])
            txt_tree = Gtk.TreeView(model=txt_store)
            txt_key_column = Gtk.TreeViewColumn(_("Name"), Gtk.CellRendererText(), text=0)
            txt_val_column = Gtk.TreeViewColumn(_("Record"), Gtk.CellRendererText(), text=1)
            txt_key_column.set_resizable(True)
            txt_val_column.set_resizable(True)
            txt_tree.append_column(txt_key_column)
            txt_tree.append_column(txt_val_column)
            txt_scroll = Gtk.ScrolledWindow()
            txt_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            txt_scroll.set_min_content_height(120)
            txt_scroll.add(txt_tree)
            txt_frame.add(txt_scroll)
            area.add(txt_frame)

        self.connect("response", self._on_response)
        self.show_all()

    def _on_response(self, _dialog: Gtk.Dialog, response_id: int) -> None:
        if response_id != Gtk.ResponseType.APPLY or not self._raw_content:
            return
        raw_dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            buttons=Gtk.ButtonsType.CLOSE,
            message_type=Gtk.MessageType.INFO,
            text=_("Raw XML"),
        )
        if self._raw_xml_location:
            raw_dialog.format_secondary_text(f"{_('XML location')}: {self._raw_xml_location}\n\n{self._raw_content}")
        else:
            raw_dialog.format_secondary_text(self._raw_content)
        raw_dialog.run()
        raw_dialog.destroy()

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
