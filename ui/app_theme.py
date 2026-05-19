# File app_theme.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Fusion color-scheme management: OS dark/light detection + runtime switching.

Entry point: call ``setup_fusion_theme(app)`` once after ``app.setStyle("Fusion")``.
Add dialog / widget stylesheet overrides here as the UI evolves.

Hover / active color contract (used by all widget stylesheets):
  - hover  → palette(alternate-base)   light blue tint, both themes
  - active → palette(highlight)        full blue, both themes
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

_LOG = logging.getLogger(__name__)

# Light blue used as hover tint in both themes (alternate-base palette role).
_HOVER_LIGHT = QColor(225, 240, 255)   # very pale blue on white backgrounds
_HOVER_DARK  = QColor(38,  58,  84)    # dark blue tint on dark backgrounds


def _make_fusion_dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(53, 53, 53))
    p.setColor(QPalette.ColorRole.WindowText,      Qt.GlobalColor.white)
    p.setColor(QPalette.ColorRole.Base,            QColor(25, 25, 25))
    p.setColor(QPalette.ColorRole.AlternateBase,   _HOVER_DARK)
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(40, 40, 40))
    p.setColor(QPalette.ColorRole.ToolTipText,     Qt.GlobalColor.white)
    p.setColor(QPalette.ColorRole.Text,            Qt.GlobalColor.white)
    p.setColor(QPalette.ColorRole.Button,          QColor(53, 53, 53))
    p.setColor(QPalette.ColorRole.ButtonText,      Qt.GlobalColor.white)
    p.setColor(QPalette.ColorRole.BrightText,      Qt.GlobalColor.red)
    p.setColor(QPalette.ColorRole.Link,            QColor(42, 130, 218))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(42, 130, 218))
    p.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)
    p.setColor(QPalette.ColorRole.Mid,             QColor(40, 40, 40))
    p.setColor(QPalette.ColorRole.Shadow,          QColor(20, 20, 20))
    _disabled = QPalette.ColorGroup.Disabled
    _grey = QColor(127, 127, 127)
    p.setColor(_disabled, QPalette.ColorRole.WindowText, _grey)
    p.setColor(_disabled, QPalette.ColorRole.Text,       _grey)
    p.setColor(_disabled, QPalette.ColorRole.ButtonText, _grey)
    p.setColor(_disabled, QPalette.ColorRole.Highlight,  QColor(80, 80, 80))
    return p


# Global stylesheet applied on QApplication for widgets without their own stylesheet.
# Covers QMenuBar top-level items (Fusion leaves them without any hover by default).
_GLOBAL_QSS = (
    "QDialog { background-color: palette(base); }"
    "QMenuBar::item:selected {"
    " background-color: palette(alternate-base);"
    " color: palette(text);"
    " border-radius: 2px;"
    "}"
    "QMenu::item:selected {"
    " background-color: palette(alternate-base);"
    " color: palette(text);"
    "}"
    "QGroupBox {"
    " background-color: palette(base);"
    " border: 1px solid palette(mid);"
    " border-radius: 4px;"
    " margin-top: 16px;"
    " padding: 8px 4px 4px 4px;"
    "}"
    "QGroupBox::title {"
    " subcontrol-origin: margin;"
    " subcontrol-position: top left;"
    " left: 8px;"
    " top: 2px;"
    " background-color: palette(base);"
    " padding: 0 4px;"
    " color: palette(text);"
    "}"
)


def _set_windows_titlebar_dark(dark: bool) -> None:
    """Tell DWM to use dark or light title bars for all current top-level windows."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        value = ctypes.c_int(1 if dark else 0)
        for widget in QApplication.topLevelWidgets():
            hwnd = int(widget.winId())
            if hwnd:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    DWMWA_USE_IMMERSIVE_DARK_MODE,
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                )
    except Exception:
        _LOG.debug("DwmSetWindowAttribute failed", exc_info=True)


def _apply_color_scheme(app: QApplication, scheme: Qt.ColorScheme) -> None:
    if scheme == Qt.ColorScheme.Dark:
        _LOG.debug("Applying Fusion dark palette")
        app.setPalette(_make_fusion_dark_palette())
    else:
        _LOG.debug("Applying Fusion light palette")
        palette = app.style().standardPalette()
        palette.setColor(QPalette.ColorRole.AlternateBase, _HOVER_LIGHT)
        app.setPalette(palette)
    app.setStyleSheet(_GLOBAL_QSS)
    dark = scheme == Qt.ColorScheme.Dark
    _set_windows_titlebar_dark(dark)
    # Deferred call covers windows not yet shown at startup (no HWND yet on first call).
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, lambda: _set_windows_titlebar_dark(dark))


def apply_color_scheme(app: QApplication, scheme: Qt.ColorScheme | None = None) -> None:
    """Apply a color scheme immediately without touching the colorSchemeChanged signal.

    Pass ``None`` to re-apply the current OS preference.
    """
    if scheme is None:
        scheme = app.styleHints().colorScheme()
    _apply_color_scheme(app, scheme)


def setup_fusion_theme(
    app: QApplication,
    *,
    forced_scheme: Qt.ColorScheme | None = None,
) -> None:
    """Apply Fusion palette and track OS color-scheme changes.

    Pass ``forced_scheme`` (Dark or Light) to lock the theme regardless of the OS setting.
    When ``None`` (default) the OS preference is used and runtime changes are tracked.
    """
    if forced_scheme is not None:
        _LOG.debug("Color scheme forced to %s", forced_scheme)
        _apply_color_scheme(app, forced_scheme)
        return
    hints = app.styleHints()
    _apply_color_scheme(app, hints.colorScheme())
    hints.colorSchemeChanged.connect(lambda scheme: _apply_color_scheme(app, scheme))
