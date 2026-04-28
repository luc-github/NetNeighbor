"""Simple GTK TreeView wrapper for discovered devices."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from model.device import Device


class DeviceList(Gtk.ScrolledWindow):
    def __init__(self) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self._store = Gtk.ListStore(str, str, int, str, str)
        self._tree = Gtk.TreeView(model=self._store)
        self._add_column("Name", 0)
        self._add_column("IP", 1)
        self._add_column("Port", 2)
        self._add_column("Category", 3)
        self._add_column("Source", 4)

        self.add(self._tree)
        self.show_all()

    def set_devices(self, devices: list[Device]) -> None:
        self._store.clear()
        for device in devices:
            self._store.append([device.name, device.ip, device.port, device.category, device.source])

    def _add_column(self, title: str, model_index: int) -> None:
        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn(title, renderer, text=model_index)
        column.set_resizable(True)
        self._tree.append_column(column)
