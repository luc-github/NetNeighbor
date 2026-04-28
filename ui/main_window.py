"""Main application window."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from discovery.manager import DiscoveryManager
from ui.device_list import DeviceList


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application, discovery_manager: DiscoveryManager) -> None:
        super().__init__(application=application, title="NetNeighbor")
        self.set_default_size(900, 560)
        self._manager = discovery_manager

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_border_width(8)
        self.add(root)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        refresh_button = Gtk.Button.new_with_label("Refresh")
        refresh_button.connect("clicked", self._on_refresh_clicked)
        header.pack_start(refresh_button, False, False, 0)
        root.pack_start(header, False, False, 0)

        self._device_list = DeviceList()
        root.pack_start(self._device_list, True, True, 0)

        self._manager.add_listener(self._on_devices_updated)
        self._manager.start()

        self.connect("destroy", self._on_destroy)
        self.show_all()

    def _on_refresh_clicked(self, _button: Gtk.Button) -> None:
        self._manager.refresh()

    def _on_devices_updated(self, devices) -> None:
        GLib.idle_add(self._device_list.set_devices, devices)

    def _on_destroy(self, *_args) -> None:
        self._manager.stop()
