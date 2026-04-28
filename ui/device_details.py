"""Device details dialog placeholder."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


class DeviceDetailsDialog(Gtk.Dialog):
    def __init__(self, parent: Gtk.Window, title: str, content: str) -> None:
        super().__init__(title=title, transient_for=parent, modal=True)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.set_default_size(600, 400)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.get_buffer().set_text(content)

        area = self.get_content_area()
        area.add(text_view)
        self.show_all()
