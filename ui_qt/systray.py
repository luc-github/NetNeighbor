# File systray.py for NetNeighbor version 1.0.0
# License: LGPL3
"""System-tray icon and context menu for NetNeighbor."""

from __future__ import annotations

from gettext import gettext as _

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


class NetNeighborTray(QSystemTrayIcon):
    """System-tray icon with Show/Hide and Quit actions.

    Pass the main window as *window*; the tray toggles its visibility on
    left-click and keeps the Show/Hide label in sync with the window state.
    """

    def __init__(self, icon: QIcon, window, parent=None) -> None:
        super().__init__(icon, parent)
        self._window = window
        self.setToolTip("NetNeighbor")
        self._menu = QMenu()
        self._action_show_hide = self._menu.addAction("")
        self._menu.addSeparator()
        self._action_quit = self._menu.addAction(_("Quit"))
        self.setContextMenu(self._menu)
        self._sync_show_hide_label()
        self._action_show_hide.triggered.connect(self._toggle_window)
        self._action_quit.triggered.connect(self._quit)
        self.activated.connect(self._on_activated)

    # ── public API ─────────────────────────────────────────────────────────────

    def notify_window_visibility_changed(self) -> None:
        """Call whenever the window is shown or hidden to keep the label in sync."""
        self._sync_show_hide_label()

    # ── internals ──────────────────────────────────────────────────────────────

    def _sync_show_hide_label(self) -> None:
        if self._window.isVisible():
            self._action_show_hide.setText(_("Hide window"))
        else:
            self._action_show_hide.setText(_("Show window"))

    def _toggle_window(self) -> None:
        if self._window.isVisible():
            self._window.hide()
        else:
            self._window.bring_to_front()
        self._sync_show_hide_label()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_window()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._window.bring_to_front()
            self._sync_show_hide_label()

    def _quit(self) -> None:
        self.hide()
        QApplication.quit()
