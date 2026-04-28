"""GTK application bootstrap for NetNeighbor."""

import os
from pathlib import Path
import socket
import threading
import tempfile

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from discovery.manager import DiscoveryManager
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
        return True
    except OSError:
        return False


class NetNeighborApplication(Gtk.Application):
    def __init__(self) -> None:
        self._app_id = "io.esp3d.netneighbor"
        super().__init__(application_id=self._app_id)
        self._manager = DiscoveryManager()
        self._window: MainWindow | None = None
        self._instance_lock = SingleInstanceLock(self._app_id)
        self._activation_server = InstanceActivationServer(self._app_id, self._present_window)

    def do_activate(self) -> None:
        if self._window is None:
            self._window = MainWindow(application=self, discovery_manager=self._manager)
        self._present_window()

    def _present_window(self) -> bool:
        if self._window is not None:
            self._window.present()
        return False


def main() -> int:
    app = NetNeighborApplication()
    if not app._instance_lock.acquire():
        if request_existing_instance_activation(app._app_id):
            print("NetNeighbor is already running. Existing window activated.")
            return 0
        print("NetNeighbor is already running.")
        return 1
    app._activation_server.start()
    try:
        return app.run([])
    finally:
        app._activation_server.stop()
        app._instance_lock.release()
