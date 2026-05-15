# File app.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""GTK application bootstrap for NetNeighbor."""

import argparse
import logging
import os
from pathlib import Path
import socket
import threading
import tempfile
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from discovery.manager import DiscoveryManager
from i18n import setup_i18n
from utils.app_logging import setup_logging as _setup_logging
from utils.discovery_config import discovery_manager_kwargs, load_discovery_protocol_config
from utils.scheduling import gtk_idle_schedule
from ui.icons import resolve_app_icon_path
from ui.main_window import MainWindow

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX environment
    fcntl = None


class SingleInstanceLock:
    """Ensures only one process instance runs on Linux."""

    def __init__(self, app_id: str) -> None:
        self._enabled = os.name == "posix" and fcntl is not None
        lock_name = app_id.replace(".", "_") + ".lock"
        self._lock_path = Path(tempfile.gettempdir()) / lock_name
        self._handle = None

    def acquire(self) -> bool:
        if not self._enabled:
            return True

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._lock_path.open("w", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._handle.close()
            self._handle = None
            return False

        self._handle.write(str(os.getpid()))
        self._handle.flush()
        return True

    def release(self) -> None:
        if not self._enabled or self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


class InstanceActivationServer:
    def __init__(self, app_id: str, on_activate) -> None:
        self._enabled = os.name == "posix"
        socket_name = app_id.replace(".", "_") + ".sock"
        self._socket_path = Path(tempfile.gettempdir()) / socket_name
        self._on_activate = on_activate
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if not self._enabled:
            return

        try:
            self._socket_path.unlink(missing_ok=True)
            self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._server.bind(str(self._socket_path))
            self._server.listen(1)
            self._server.settimeout(0.5)
        except OSError:
            self.stop()
            return

        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        if self._server is None:
            return
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    data = conn.recv(64).decode("utf-8").strip()
                except OSError:
                    data = ""
                if data == "ACTIVATE":
                    GLib.idle_add(self._on_activate)
                    try:
                        conn.sendall(b"OK")
                    except OSError:
                        pass

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._enabled:
            self._socket_path.unlink(missing_ok=True)


def request_existing_instance_activation(app_id: str) -> bool:
    if os.name != "posix":
        return False
    socket_path = Path(tempfile.gettempdir()) / (app_id.replace(".", "_") + ".sock")
    if not socket_path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            client.connect(str(socket_path))
            client.sendall(b"ACTIVATE")
            reply = client.recv(16).decode("utf-8").strip()
        return reply == "OK"
    except OSError:
        return False


def _suppress_gdk_freeze_critical() -> None:
    """Permanently silence the spurious Gdk-CRITICAL from GTK 3's dialog freeze/thaw bug.

    GTK 3 has a long-standing bug where ``gdk_window_thaw_toplevel_updates_libgtk_only``
    is called one extra time when nested ``Gtk.Dialog.run()`` calls are used, triggering:
        Gdk-CRITICAL: gdk_window_thaw_toplevel_updates: assertion
            'window->update_and_descendants_freeze_count > 0' failed
    This is harmless (the assert just fires, no state is corrupted) but noisy.
    Install a permanent GLib log handler that drops this one specific message.
    """
    _NEEDLE = "gdk_window_thaw_toplevel_updates"

    def _filter(domain, level, message, _user_data):
        if message and _NEEDLE in message:
            return
        GLib.log_default_handler(domain, level, message, None)

    GLib.log_set_handler(
        "Gdk",
        GLib.LogLevelFlags.LEVEL_CRITICAL,
        _filter,
        None,
    )


class NetNeighborApplication(Gtk.Application):
    def __init__(self, *, start_minimized_to_tray: bool = False) -> None:
        self._app_id = "io.esp3d.netneighbor"
        self._cli_start_minimized_to_tray = bool(start_minimized_to_tray)
        setup_i18n()
        super().__init__(application_id=self._app_id)
        proto_cfg = load_discovery_protocol_config()
        merge_cfg = proto_cfg.get("merge") if isinstance(proto_cfg.get("merge"), dict) else {}
        show_ip_in_device_list = bool(merge_cfg.get("show_ip_in_device_list", True))
        dm_kw = discovery_manager_kwargs(proto_cfg)
        dm_kw["schedule_on_main_thread"] = gtk_idle_schedule
        self._manager = DiscoveryManager(**dm_kw)
        information_precedence = dm_kw["information_precedence"]
        startup_refresh_seconds = proto_cfg.get("startup_refresh_seconds")
        if not isinstance(startup_refresh_seconds, list) or not startup_refresh_seconds:
            startup_refresh_seconds = [15, 30, 60, 120, 300, 600]
        self._startup_refresh_seconds = [int(v) for v in startup_refresh_seconds]
        self._information_precedence = information_precedence
        self._show_ip_in_device_list = show_ip_in_device_list
        self._window: MainWindow | None = None
        self._instance_lock = SingleInstanceLock(self._app_id)
        self._activation_server = InstanceActivationServer(self._app_id, self._present_window)
        self._set_default_app_icon()
        # Disable GTK's "use header bar in dialogs" setting globally so that all
        # Gtk.Dialog instances use traditional title bars + action-area buttons.
        # This must be done before any dialog is created.
        try:
            Gtk.Settings.get_default().set_property("gtk-dialogs-use-header", False)
        except Exception:
            pass
        _suppress_gdk_freeze_critical()

    def do_activate(self) -> None:
        created = False
        if self._window is None:
            created = True
            self._window = MainWindow(
                application=self,
                discovery_manager=self._manager,
                startup_refresh_seconds=self._startup_refresh_seconds,
                information_precedence=self._information_precedence,
                show_ip_in_device_list=self._show_ip_in_device_list,
                cli_start_minimized_to_tray=self._cli_start_minimized_to_tray,
            )
        # Do not steal focus before the tray/minimize idle runs (CLI/session autostart).
        if created and self._cli_start_minimized_to_tray:
            return
        self._present_window()

    def _present_window(self) -> bool:
        if self._window is not None:
            self._window.show_all()
            self._window.deiconify()
            self._window.present()
            try:
                event_time = Gtk.get_current_event_time()
                if event_time == 0:
                    event_time = int(time.monotonic() * 1000) & 0xFFFFFFFF
                self._window.present_with_time(event_time)
            except Exception:
                pass
            self._window.set_urgency_hint(True)
            GLib.timeout_add(250, self._clear_urgency_hint)
            self._window.grab_focus()
        return False

    def _clear_urgency_hint(self) -> bool:
        if self._window is not None:
            self._window.set_urgency_hint(False)
        return False

    def _set_default_app_icon(self) -> None:
        icon_path = resolve_app_icon_path()
        if icon_path is None:
            return
        try:
            Gtk.Window.set_default_icon_from_file(str(icon_path))
        except Exception:
            return


def main(argv: list[str] | None = None) -> int:
    import sys

    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(
        prog=Path(argv[0]).name if argv else "netneighbor",
        description="NetNeighbor — LAN discovery (SSDP/mDNS, GTK)",
    )
    parser.add_argument(
        "--start-minimized-to-tray",
        action="store_true",
        help="Start with the main window hidden in the tray (session autostart; same as prefs when tray exists).",
    )
    parsed, gtk_remainder = parser.parse_known_args(argv[1:])
    gtk_argv = [argv[0], *gtk_remainder]

    _setup_logging()
    app = NetNeighborApplication(start_minimized_to_tray=parsed.start_minimized_to_tray)
    if not app._instance_lock.acquire():
        if request_existing_instance_activation(app._app_id):
            print("NetNeighbor is already running. Existing window activated.")
            return 0
        print("NetNeighbor is already running.")
        return 1
    app._activation_server.start()
    try:
        return app.run(gtk_argv)
    finally:
        app._activation_server.stop()
        app._instance_lock.release()
