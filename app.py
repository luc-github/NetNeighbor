"""GTK application bootstrap for NetNeighbor."""

import argparse
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
        mdns_cfg = proto_cfg.get("mdns")
        ssdp_cfg = proto_cfg.get("ssdp")
        wsd_cfg = proto_cfg.get("wsd")
        nmb_cfg = proto_cfg.get("nmb")
        wsdd_cfg = proto_cfg.get("wsdd")
        if not isinstance(mdns_cfg, dict):
            mdns_cfg = {}
        if not isinstance(ssdp_cfg, dict):
            ssdp_cfg = {}
        if not isinstance(wsd_cfg, dict):
            wsd_cfg = {}
        if not isinstance(nmb_cfg, dict):
            nmb_cfg = {}
        if not isinstance(wsdd_cfg, dict):
            wsdd_cfg = {}
        mdns_q = mdns_cfg.get("query") if isinstance(mdns_cfg.get("query"), dict) else {}
        ssdp_q = ssdp_cfg.get("query") if isinstance(ssdp_cfg.get("query"), dict) else {}
        wsd_q = wsd_cfg.get("query") if isinstance(wsd_cfg.get("query"), dict) else {}
        nmb_q = nmb_cfg.get("query") if isinstance(nmb_cfg.get("query"), dict) else {}
        wx_q = wsdd_cfg.get("query") if isinstance(wsdd_cfg.get("query"), dict) else {}
        merge_cfg = proto_cfg.get("merge") if isinstance(proto_cfg.get("merge"), dict) else {}
        protocol_order = merge_cfg.get("protocol_order")
        order_list: list[str] | None = None
        if isinstance(protocol_order, list) and protocol_order:
            order_list = [str(x).strip().lower() for x in protocol_order if isinstance(x, str) and str(x).strip()]
            order_list = list(dict.fromkeys(order_list))
        ipc = merge_cfg.get("information_precedence")
        information_precedence = normalize_information_precedence_list(ipc if isinstance(ipc, list) else None)
        wsdd_listen = wx_q.get("listen") if isinstance(wx_q.get("listen"), str) else ""
        wsdd_listen = str(wsdd_listen).strip()
        enable_wsdd_socket = bool(wsdd_cfg.get("enabled", False)) and bool(wsdd_listen)
        show_ip_in_device_list = bool(merge_cfg.get("show_ip_in_device_list", True))
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
            enable_wsd=bool(wsd_cfg.get("enabled", True)),
            wsd_interval_seconds=wsd_q.get("interval_seconds"),
            wsd_timeout_seconds=wsd_q.get("timeout_seconds"),
            enable_wsdd_socket=enable_wsdd_socket,
            wsdd_listen=wsdd_listen or None,
            wsdd_interval_seconds=wx_q.get("interval_seconds"),
            wsdd_socket_timeout_seconds=wx_q.get("socket_timeout_seconds"),
            wsdd_probe_each_poll=bool(wx_q.get("probe_each_poll", True)),
            enable_nmb=bool(nmb_cfg.get("enabled", True)),
            nmb_interval_seconds=nmb_q.get("interval_seconds"),
            nmb_timeout_seconds=nmb_q.get("timeout_seconds"),
            nmb_argv=nmb_q.get("argv") if isinstance(nmb_q.get("argv"), list) else None,
            nmb_directed_ips=nmb_q.get("directed_ips") if isinstance(nmb_q.get("directed_ips"), list) else None,
            protocol_merge_order=order_list,
            information_precedence=information_precedence,
        )
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
        "default": "NONE",
        "app": "NONE",
        "device_list": "NONE",
        "ssdp": "NONE",
        "mdns": "NONE",
        "wsd": "NONE",
        "wsdd": "NONE",
        "nmb": "NONE",
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
    wsd_level = _to_level(str(config.get("wsd", config.get("default", "INFO"))), logging.INFO)
    wsdd_level = _to_level(str(config.get("wsdd", config.get("default", "INFO"))), logging.INFO)
    nmb_level = _to_level(str(config.get("nmb", config.get("default", "INFO"))), logging.INFO)
    device_list_level = _to_level(str(config.get("device_list", config.get("default", "INFO"))), logging.INFO)

    for logger_name in ("app", "ui", "utils", "discovery.manager", "model"):
        logging.getLogger(logger_name).setLevel(app_level)
    logging.getLogger("discovery.ssdp").setLevel(ssdp_level)
    logging.getLogger("discovery.mdns").setLevel(mdns_level)
    logging.getLogger("discovery.wsd").setLevel(wsd_level)
    logging.getLogger("discovery.wsdd_client").setLevel(wsdd_level)
    logging.getLogger("discovery.netbios").setLevel(nmb_level)
    # Manager-side messages tied to a protocol (device add/update, SSDP merge) use child loggers.
    logging.getLogger("discovery.manager.ssdp").setLevel(ssdp_level)
    logging.getLogger("discovery.manager.mdns").setLevel(mdns_level)
    logging.getLogger("discovery.manager.wsd").setLevel(wsd_level)
    logging.getLogger("discovery.manager.wsdd").setLevel(wsdd_level)
    logging.getLogger("discovery.manager.nmb").setLevel(nmb_level)
    logging.getLogger("ui.device_list").setLevel(device_list_level)
