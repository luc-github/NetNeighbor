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
        "--qt-native-style",
        action="store_true",
        help="Use the native OS widget style instead of Fusion",
    )
    parser.add_argument(
        "--theme-light",
        action="store_true",
        help="Force light theme (overrides OS setting, Fusion only)",
    )
    parser.add_argument(
        "--theme-dark",
        action="store_true",
        help="Force dark theme (overrides OS setting, Fusion only)",
    )
    parser.add_argument(
        "--start-minimized-to-tray",
        action="store_true",
        help="Start hidden in the system tray (autostart mode)",
    )
    args, _unknown = parser.parse_known_args(argv[1:])
    if args.qt_diag_toplevels:
        os.environ["NETNEIGHBOR_DEBUG_TOPLEVEL"] = "1"

    if sys.platform == "win32":
        try:
            import ctypes

            # Must run before QApplication / any HWND so the taskbar uses our icon, not python.exe.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("io.esp3d.netneighbor.qt.1")
        except OSError:
            pass

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from discovery.manager import DiscoveryManager
    from i18n import setup_i18n
    from model.device import Device
    from ui.icons import resolve_app_icon_paths_in_order
    from ui_qt import MainThreadScheduler, NetNeighborMainWindow, NetNeighborTray
    from utils.app_logging import setup_logging
    from utils.discovery_config import discovery_manager_kwargs, load_discovery_protocol_config
    from utils.qt_single_instance import create_activation_listener, send_activate_to_primary

    setup_logging(log_name="app_qt")
    setup_i18n()
    _log = logging.getLogger("app_qt")
    _log.info("NetNeighbor Qt bootstrap (logging to ~/.cache/netneighbor/netneighbor.log)")

    app = QApplication(argv)
    if not args.qt_native_style:
        from PySide6.QtCore import Qt
        from ui_qt.app_theme import setup_fusion_theme
        app.setStyle("Fusion")
        if args.theme_dark:
            forced: Qt.ColorScheme | None = Qt.ColorScheme.Dark
        elif args.theme_light:
            forced = Qt.ColorScheme.Light
        else:
            from utils.ui_prefs import load_ui_preferences
            _theme_pref = load_ui_preferences().get("theme", "auto")
            if _theme_pref == "light":
                forced = Qt.ColorScheme.Light
            elif _theme_pref == "dark":
                forced = Qt.ColorScheme.Dark
            else:
                forced = None
        setup_fusion_theme(app, forced_scheme=forced)

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
    app_icon = app.windowIcon()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)

    _tray: NetNeighborTray | None = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        _tray = NetNeighborTray(app.windowIcon(), window, parent=app)
        _tray.show()
        app._qt_tray = _tray

    _start_hidden = args.start_minimized_to_tray and _tray is not None
    if not _start_hidden:
        window.show()

    if os.environ.get("NETNEIGHBOR_DEBUG_TOPLEVEL"):
        from ui_qt.main_window import _debug_log_extra_top_level_widgets

        QTimer.singleShot(2500, lambda: _debug_log_extra_top_level_widgets("idle 2.5s after show"))

    startup_refresh_seconds = proto_cfg.get("startup_refresh_seconds")
    if not isinstance(startup_refresh_seconds, list) or not startup_refresh_seconds:
        startup_refresh_seconds = [15, 30, 60, 120, 300, 600]
    startup_refresh_seconds = [int(v) for v in startup_refresh_seconds]

    def _run_startup_refresh_once(delay_seconds: int) -> None:
        try:
            _log.info("Startup discovery refresh (+%ss from launch)", delay_seconds)
            manager.refresh()
        except Exception:
            _log.debug("Startup auto-refresh failed at +%ss", delay_seconds, exc_info=True)

    def _start_discovery() -> None:
        """Run in a background thread so the Qt event loop (and overlay) stay responsive."""
        import threading as _threading
        def _run() -> None:
            manager.start()
            try:
                manager.refresh()
            except Exception:
                _log.debug("Post-start discovery refresh failed", exc_info=True)
        _threading.Thread(target=_run, daemon=True, name="discovery-start").start()

    for delay in startup_refresh_seconds:
        if delay > 0:
            QTimer.singleShot(delay * 1000, lambda d=delay: _run_startup_refresh_once(d))

    # Defer start until the first event-loop tick so the window (and overlay) paint first.
    QTimer.singleShot(0, _start_discovery)

    try:
        return int(app.exec())
    finally:
        manager.stop()


if __name__ == "__main__":
    raise SystemExit(main())
