# File preferences_dialog.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Preferences dialog — tabs mirror View → Preferences (partial 2.0 implementation)."""

from __future__ import annotations

from gettext import gettext as _

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from utils.session_autostart import apply_autostart_pref, autostart_enabled_on_disk
from utils.ui_prefs import load_ui_preferences, save_ui_preferences


class PreferencesDialog(QDialog):
    """Modal preferences: General (startup/session), Notifications, More (stub editors)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Preferences"))
        self.setModal(True)
        self.resize(520, 380)

        self._prefs = load_ui_preferences()

        tabs = QTabWidget()

        tabs.addTab(self._build_general_tab(), _("General"))
        tabs.addTab(self._build_notifications_tab(), _("Notifications"))
        tabs.addTab(self._build_more_tab(), _("More"))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

        self._load_widgets()

    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        session = QGroupBox(_("Session"))
        sl = QVBoxLayout(session)
        self._close_to_tray = QCheckBox(_("Close to system tray / panel instead of exiting"))
        self._start_minimized = QCheckBox(_("Start minimized to tray"))
        sl.addWidget(self._close_to_tray)
        sl.addWidget(self._start_minimized)

        startup = QGroupBox(_("Startup"))
        ul = QVBoxLayout(startup)
        self._start_at_login = QCheckBox(_("Start NetNeighbor when logging in"))
        ul.addWidget(self._start_at_login)
        hint = QLabel(
            _(
                "On Linux, this writes an XDG autostart entry. "
                "Windows and macOS startup integration will follow in a later release."
            )
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        ul.addWidget(hint)

        outer.addWidget(session)
        outer.addWidget(startup)
        outer.addStretch(1)
        return page

    def _build_notifications_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        group = QGroupBox(_("Desktop notifications"))
        gl = QVBoxLayout(group)

        self._notif_group = QButtonGroup(self)
        self._notif_off = QRadioButton(_("Notifications off"))
        self._notif_monitored = QRadioButton(_("Monitored devices only"))
        self._notif_all = QRadioButton(_("All devices"))

        self._notif_group.addButton(self._notif_off, 0)
        self._notif_group.addButton(self._notif_monitored, 1)
        self._notif_group.addButton(self._notif_all, 2)

        gl.addWidget(self._notif_off)
        gl.addWidget(self._notif_monitored)
        gl.addWidget(self._notif_all)

        note = QLabel(
            _(
                "Notification delivery depends on the OS and session. "
                "Tray-backed behaviour (close to tray, minimized start) will align once the system tray is implemented."
            )
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")

        layout.addWidget(group)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _build_more_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        intro = QLabel(
            _("The following editors are available in the legacy GTK build; they will be added here incrementally:")
        )
        intro.setWordWrap(True)

        btn_locations = QPushButton(_("Location presets…"))
        btn_locations.clicked.connect(self._stub_location_presets)

        btn_types = QPushButton(_("Type presets…"))
        btn_types.clicked.connect(self._stub_type_presets)

        btn_external = QPushButton(_("External applications…"))
        btn_external.clicked.connect(self._stub_external_apps)

        for b in (btn_locations, btn_types, btn_external):
            b.setMinimumHeight(28)

        layout.addWidget(intro)
        layout.addWidget(btn_locations)
        layout.addWidget(btn_types)
        layout.addWidget(btn_external)
        layout.addStretch(1)
        return page

    def _load_widgets(self) -> None:
        self._close_to_tray.setChecked(bool(self._prefs.get("close_to_tray", True)))
        self._start_minimized.setChecked(bool(self._prefs.get("start_minimized_to_tray", False)))
        self._start_at_login.setChecked(
            bool(self._prefs.get("start_at_login", autostart_enabled_on_disk()))
        )

        mode = str(self._prefs.get("notification_mode", "off"))
        if mode == "monitored":
            self._notif_monitored.setChecked(True)
        elif mode == "all":
            self._notif_all.setChecked(True)
        else:
            self._notif_off.setChecked(True)

    def _accept_save(self) -> None:
        prefs = dict(self._prefs)
        prefs["close_to_tray"] = self._close_to_tray.isChecked()
        prefs["start_minimized_to_tray"] = self._start_minimized.isChecked()
        prefs["start_at_login"] = self._start_at_login.isChecked()

        if self._notif_monitored.isChecked():
            prefs["notification_mode"] = "monitored"
        elif self._notif_all.isChecked():
            prefs["notification_mode"] = "all"
        else:
            prefs["notification_mode"] = "off"

        save_ui_preferences(prefs)
        apply_autostart_pref(bool(prefs.get("start_at_login")))
        self.accept()

    def _stub_location_presets(self) -> None:
        QMessageBox.information(
            self,
            _("Location presets"),
            _("This editor is not available in this build yet."),
        )

    def _stub_type_presets(self) -> None:
        QMessageBox.information(
            self,
            _("Type presets"),
            _("This editor is not available in this build yet."),
        )

    def _stub_external_apps(self) -> None:
        QMessageBox.information(
            self,
            _("External applications"),
            _("This editor is not available in this build yet."),
        )
