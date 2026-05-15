# File app_qt.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Application bootstrap — NetNeighbor 2.0 (PySide6)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(
        prog=Path(argv[0]).name if argv else "netneighbor",
        description="NetNeighbor — LAN discovery",
    )
    parser.add_argument(
        "--qt-diag-toplevels",
        action="store_true",
        help="Log extra Qt top-level QWidget/QWindow (set NETNEIGHBOR_DEBUG_TOPLEVEL; uses INFO lines)",
    )
    parser.add_argument(
        "--qt-fusion-style",
        action="store_true",
        help="Use Fusion widget style (test: avoids native Windows style in icon lists)",
    )
    args, _unknown = parser.parse_known_args(argv[1:])
    if args.qt_diag_toplevels:
        os.environ["NETNEIGHBOR_DEBUG_TOPLEVEL"] = "1"

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from discovery.manager import DiscoveryManager
    from i18n import setup_i18n
    from model.device import Device
    from ui.icons import resolve_app_icon_paths_in_order
    from ui_qt import MainThreadScheduler, NetNeighborMainWindow
    from utils.app_logging import setup_logging
    from utils.discovery_config import discovery_manager_kwargs, load_discovery_protocol_config
    from utils.qt_single_instance import create_activation_listener, send_activate_to_primary

    setup_logging(log_name="app_qt")
    setup_i18n()
    _log = logging.getLogger("app_qt")

    app = QApplication(argv)
    if args.qt_fusion_style:
        app.setStyle("Fusion")

    if sys.platform == "win32":
        try:
            import ctypes

            # Lets the taskbar use our window icon instead of grouping under python.exe only.
            # Note: this AppUserModelID applies to the whole process — any stray top-level HWND
            # (e.g. from native styling) can briefly show the same icon / a truncated "python…" title.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("io.esp3d.netneighbor.qt.1")
        except OSError:
            pass

    for icon_path in resolve_app_icon_paths_in_order():
        app_icon = QIcon(str(icon_path))
        if not app_icon.isNull():
            app.setWindowIcon(app_icon)
            break

    if send_activate_to_primary():
        print("NetNeighbor is already running - existing window brought to the foreground.")
        return 0

    scheduler = MainThreadScheduler(app)

    proto_cfg = load_discovery_protocol_config()
    dm_kw = discovery_manager_kwargs(proto_cfg)
    dm_kw["schedule_on_main_thread"] = lambda fn: scheduler.invoke.emit(fn)
    manager = DiscoveryManager(**dm_kw)

    window = NetNeighborMainWindow(
        discovery_manager=manager,
        information_precedence=list(dm_kw["information_precedence"]),
    )
    app_icon = app.windowIcon()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    app._qt_main_window = window

    activation_server = create_activation_listener(app, on_activate=window.bring_to_front)
    if activation_server is None:
        _log.warning("Single-instance server unavailable (pipe busy or stale).")
        if send_activate_to_primary():
            print("NetNeighbor is already running - existing window brought to the foreground.")
            return 0
    else:
        app._qt_activation_server = activation_server

    def on_devices(devices: list[Device]) -> None:
        def update() -> None:
            window.set_devices(devices)

        scheduler.invoke.emit(update)

    manager.add_listener(on_devices)

    window.resize(960, 520)
    window.show()

    if os.environ.get("NETNEIGHBOR_DEBUG_TOPLEVEL"):
        from PySide6.QtCore import QTimer

        from ui_qt.main_window import _debug_log_extra_top_level_widgets

        QTimer.singleShot(2500, lambda: _debug_log_extra_top_level_widgets("idle 2.5s after show"))

    manager.start()
    try:
        return int(app.exec())
    finally:
        manager.stop()


if __name__ == "__main__":
    raise SystemExit(main())
