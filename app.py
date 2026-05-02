"""GTK application bootstrap for NetNeighbor."""

import logging
import os
from pathlib import Path
import socket
import threading
import tempfile
import time
import json

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from discovery.manager import DiscoveryManager
from i18n import setup_i18n
from utils.discovery_config import load_discovery_protocol_config, normalize_information_precedence_list
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


class NetNeighborApplication(Gtk.Application):
    def __init__(self) -> None:
        self._app_id = "io.esp3d.netneighbor"
        setup_i18n()
        super().__init__(application_id=self._app_id)
        proto_cfg = load_discovery_protocol_config()
        mdns_cfg = proto_cfg.get("mdns")
        ssdp_cfg = proto_cfg.get("ssdp")
        if not isinstance(mdns_cfg, dict):
            mdns_cfg = {}
        if not isinstance(ssdp_cfg, dict):
            ssdp_cfg = {}
        mdns_q = mdns_cfg.get("query") if isinstance(mdns_cfg.get("query"), dict) else {}
        ssdp_q = ssdp_cfg.get("query") if isinstance(ssdp_cfg.get("query"), dict) else {}
        merge_cfg = proto_cfg.get("merge") if isinstance(proto_cfg.get("merge"), dict) else {}
        protocol_order = merge_cfg.get("protocol_order")
        order_list: list[str] | None = None
        if isinstance(protocol_order, list) and protocol_order:
            order_list = [str(x).strip().lower() for x in protocol_order if isinstance(x, str) and str(x).strip()]
            order_list = list(dict.fromkeys(order_list))
        ipc = merge_cfg.get("information_precedence")
        information_precedence = normalize_information_precedence_list(ipc if isinstance(ipc, list) else None)
        self._manager = DiscoveryManager(
            enable_ssdp=bool(ssdp_cfg.get("enabled", True)),
            enable_mdns=bool(mdns_cfg.get("enabled", True)),
            enable_ssdp_rules=bool(ssdp_cfg.get("rules", True)),
            enable_mdns_rules=bool(mdns_cfg.get("rules", True)),
            ssdp_query_interval_seconds=ssdp_q.get("interval_seconds"),
            ssdp_mx_seconds=ssdp_q.get("mx_seconds"),
            ssdp_descriptor_http_min_interval_seconds=ssdp_q.get("descriptor_http_min_interval_seconds"),
            mdns_enumeration_timeout_seconds=mdns_q.get("enumeration_timeout_seconds"),
            mdns_enumeration_interval_seconds=mdns_q.get("enumeration_interval_seconds"),
            mdns_service_info_timeout_ms=mdns_q.get("service_info_timeout_ms"),
            protocol_merge_order=order_list,
            information_precedence=information_precedence,
        )
        startup_refresh_seconds = proto_cfg.get("startup_refresh_seconds")
        if not isinstance(startup_refresh_seconds, list) or not startup_refresh_seconds:
            startup_refresh_seconds = [20, 45, 90]
        self._startup_refresh_seconds = [int(v) for v in startup_refresh_seconds]
        self._information_precedence = information_precedence
        self._window: MainWindow | None = None
        self._instance_lock = SingleInstanceLock(self._app_id)
        self._activation_server = InstanceActivationServer(self._app_id, self._present_window)
        self._set_default_app_icon()

    def do_activate(self) -> None:
        if self._window is None:
            self._window = MainWindow(
                application=self,
                discovery_manager=self._manager,
                startup_refresh_seconds=self._startup_refresh_seconds,
                information_precedence=self._information_precedence,
            )
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


def main() -> int:
    _setup_logging()
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


def _setup_logging() -> None:
    env_level_name = os.getenv("NETNEIGHBOR_LOG_LEVEL")
    config = _load_logging_config()
    default_level_name = str(config.get("default", "INFO")).upper()
    if env_level_name:
        default_level_name = env_level_name.upper()
    level = _to_level(default_level_name, logging.INFO)
    log_dir = Path.home() / ".cache" / "netneighbor"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "netneighbor.log"

    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.setLevel(level)
        _apply_named_log_levels(config)
        return

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)
    _apply_named_log_levels(config)
    logging.getLogger(__name__).info("Logging initialized at %s (%s)", default_level_name, log_file)


_LOG_LEVEL_OFF = 100  # above CRITICAL (50): suppress all standard levels


def _to_level(level_name: str, fallback: int) -> int:
    normalized = str(level_name).strip().upper()
    if normalized in {"NONE", "OFF", "DISABLED", "SILENT"}:
        return _LOG_LEVEL_OFF
    resolved = getattr(logging, normalized, None)
    if isinstance(resolved, int):
        return resolved
    return fallback


def _load_logging_config() -> dict:
    config_dir = Path.home() / ".config" / "netneighbor"
    config_path = config_dir / "logging.json"
    default_config = {
        "default": "INFO",
        "app": "INFO",
        "ssdp": "WARNING",
        "mdns": "DEBUG",
    }
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        if not config_path.exists():
            config_path.write_text(json.dumps(default_config, indent=2, sort_keys=True), encoding="utf-8")
            return dict(default_config)
        parsed = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            return dict(default_config)
        merged = dict(default_config)
        merged.update(parsed)
        return merged
    except (OSError, json.JSONDecodeError):
        return dict(default_config)


def _apply_named_log_levels(config: dict) -> None:
    app_level = _to_level(str(config.get("app", config.get("default", "INFO"))), logging.INFO)
    ssdp_level = _to_level(str(config.get("ssdp", config.get("default", "INFO"))), logging.INFO)
    mdns_level = _to_level(str(config.get("mdns", config.get("default", "INFO"))), logging.INFO)

    for logger_name in ("app", "ui", "utils", "discovery.manager", "model"):
        logging.getLogger(logger_name).setLevel(app_level)
    logging.getLogger("discovery.ssdp").setLevel(ssdp_level)
    logging.getLogger("discovery.mdns").setLevel(mdns_level)
    # Manager-side messages tied to a protocol (device add/update, SSDP merge) use child loggers.
    logging.getLogger("discovery.manager.ssdp").setLevel(ssdp_level)
    logging.getLogger("discovery.manager.mdns").setLevel(mdns_level)
