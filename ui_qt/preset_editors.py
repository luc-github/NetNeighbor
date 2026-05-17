# File preset_editors.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Modal editors for Location presets, Type presets, and External applications."""

from __future__ import annotations

from gettext import gettext as _

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

_LIST_QSS = (
    "QListWidget { background-color: palette(base); outline: none; }"
    "QListWidget::item { padding: 4px 6px; border: 1px solid transparent; }"
    "QListWidget::item:hover { background-color: palette(alternate-base); }"
    "QListWidget::item:selected,"
    "QListWidget::item:selected:active,"
    "QListWidget::item:selected:!active { background-color: palette(highlight); color: palette(highlighted-text); }"
    "QListWidget::item:selected:hover { background-color: palette(highlight); color: palette(highlighted-text); }"
)

_TREE_QSS = (
    "QTreeWidget { background-color: palette(base); outline: none; show-decoration-selected: 1; }"
    "QTreeWidget::item { padding: 4px 6px; }"
    "QTreeWidget::item:hover { background-color: palette(alternate-base); }"
    "QTreeWidget::item:selected,"
    "QTreeWidget::item:selected:active,"
    "QTreeWidget::item:selected:!active { background-color: palette(highlight); color: palette(highlighted-text); }"
    "QTreeWidget::item:selected:hover { background-color: palette(highlight); color: palette(highlighted-text); }"
)

from utils.connect_launcher import default_connect_command_templates
from utils.location_label import normalize_location_options

_SCHEME_ROWS: list[tuple[str, str]] = [
    ("http",   "HTTP"),
    ("https",  "HTTPS"),
    ("smb",    "SMB"),
    ("ftp",    "FTP"),
    ("ssh",    "SSH"),
    ("telnet", "Telnet"),
    ("sftp",   "SFTP"),
]


def _default_location_presets() -> list[str]:
    return normalize_location_options([
        _("Office"), _("Room"), _("Living room"),
        _("Kitchen"), _("Workshop"), _("Garage"),
    ])


def _default_type_options() -> list[tuple[str, str]]:
    return [
        (_("NAS"),                     "nas"),
        (_("Computer"),                "computer"),
        (_("Router"),                  "router"),
        (_("Media server"),            "mediaserver"),
        (_("Printer"),                 "printer"),
        (_("Multifunction printer"),   "multifunction_printer"),
        (_("Printer (network / IPP)"), "networkprinter"),
        (_("SmartSpeaker"),            "smartspeaker"),
        (_("SmartTV"),                 "smarttv"),
        (_("SmartDevice"),             "smartdevice"),
        (_("Camera"),                  "camera"),
        (_("HomeAppliance"),           "homeappliance"),
        (_("CNC"),                     "cnc"),
        (_("3D printer"),              "3dprinter"),
    ]


# ---------------------------------------------------------------------------
# Location presets
# ---------------------------------------------------------------------------

class LocationPresetsDialog(QDialog):
    """List editor for room / location preset strings."""

    def __init__(
        self,
        parent,
        *,
        initial_options: list[str],
        auto_add: bool,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Location presets"))
        self.resize(440, 400)

        intro = QLabel(_("Manage location presets shown in the right-click menu."))
        intro.setWordWrap(True)

        self._auto_add_chk = QCheckBox(
            _("Automatically add locations discovered on the network to this list")
        )
        self._auto_add_chk.setChecked(auto_add)

        self._list = QListWidget()
        self._list.setStyleSheet(_LIST_QSS)
        for opt in (initial_options or _default_location_presets()):
            self._list.addItem(opt)
        self._list.clearSelection()

        btn_add           = QPushButton(_("Add"))
        self._btn_rename  = QPushButton(_("Rename"))
        self._btn_remove  = QPushButton(_("Remove"))
        btn_clear         = QPushButton(_("Clear all"))
        btn_add.clicked.connect(self._on_add)
        self._btn_rename.clicked.connect(self._on_rename)
        self._btn_remove.clicked.connect(self._on_remove)
        btn_clear.clicked.connect(self._on_clear)

        self._list.currentRowChanged.connect(self._update_buttons)
        self._update_buttons(-1)

        ctrl = QHBoxLayout()
        ctrl.addWidget(btn_add)
        ctrl.addWidget(self._btn_rename)
        ctrl.addWidget(self._btn_remove)
        ctrl.addWidget(btn_clear)
        ctrl.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addSpacing(4)
        layout.addWidget(self._auto_add_chk)
        layout.addWidget(self._list, 1)
        layout.addLayout(ctrl)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------

    def _update_buttons(self, row: int) -> None:
        has = row >= 0
        self._btn_rename.setEnabled(has)
        self._btn_remove.setEnabled(has)

    def _on_add(self) -> None:
        text, ok = QInputDialog.getText(self, _("Add location preset"), _("Location name:"))
        text = text.strip()
        if not ok or not text or text in self._existing_values():
            return
        self._list.addItem(text)

    def _on_rename(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        old = item.text()
        text, ok = QInputDialog.getText(
            self, _("Rename location preset"), _("New name:"), text=old
        )
        text = text.strip()
        if not ok or not text or text == old or text in self._existing_values():
            return
        item.setText(text)

    def _on_remove(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self._list.takeItem(row)
            self._update_buttons(self._list.currentRow())

    def _on_clear(self) -> None:
        self._list.clear()
        self._update_buttons(-1)

    def _existing_values(self) -> list[str]:
        return [self._list.item(i).text() for i in range(self._list.count())]

    # ------------------------------------------------------------------

    def result_options(self) -> list[str]:
        return normalize_location_options(self._existing_values())

    def result_auto_add(self) -> bool:
        return self._auto_add_chk.isChecked()


# ---------------------------------------------------------------------------
# Type presets
# ---------------------------------------------------------------------------

class _TypeEntryDialog(QDialog):
    """Small form to enter / edit a (label, slug) pair."""

    def __init__(self, parent, *, title: str, label: str = "", slug: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedWidth(340)

        self._label_edit = QLineEdit(label)
        self._slug_edit  = QLineEdit(slug)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel(_("Label:")),   0, 0)
        grid.addWidget(self._label_edit,       0, 1)
        grid.addWidget(QLabel(_("Type ID:")), 1, 0)
        grid.addWidget(self._slug_edit,        1, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addWidget(buttons)

    def result_entry(self) -> tuple[str, str]:
        return self._label_edit.text().strip(), self._slug_edit.text().strip().lower()


class TypePresetsDialog(QDialog):
    """Two-column editor for device type presets (label + slug)."""

    def __init__(self, parent, *, initial_options: list[tuple[str, str]]) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Type presets"))
        self.resize(480, 420)

        intro = QLabel(_("Manage device type presets shown in the right-click menu."))
        intro.setWordWrap(True)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels([_("Label"), _("Type ID")])
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setRootIsDecorated(False)
        self._tree.setAllColumnsShowFocus(True)
        self._tree.setStyleSheet(_TREE_QSS)

        for label, slug in (initial_options or _default_type_options()):
            self._append_row(label, slug)
        self._tree.clearSelection()
        self._tree.setCurrentItem(None)

        btn_add          = QPushButton(_("Add"))
        self._btn_edit   = QPushButton(_("Edit"))
        self._btn_remove = QPushButton(_("Remove"))
        btn_restore      = QPushButton(_("Restore defaults"))
        btn_add.clicked.connect(self._on_add)
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_remove.clicked.connect(self._on_remove)
        btn_restore.clicked.connect(self._on_restore)

        self._tree.currentItemChanged.connect(
            lambda cur, _prev: self._update_buttons(cur is not None)
        )
        self._update_buttons(False)

        ctrl = QHBoxLayout()
        ctrl.addWidget(btn_add)
        ctrl.addWidget(self._btn_edit)
        ctrl.addWidget(self._btn_remove)
        ctrl.addStretch(1)
        ctrl.addWidget(btn_restore)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self._tree, 1)
        layout.addLayout(ctrl)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------

    def _update_buttons(self, has: bool) -> None:
        self._btn_edit.setEnabled(has)
        self._btn_remove.setEnabled(has)

    def _append_row(self, label: str, slug: str) -> None:
        QTreeWidgetItem(self._tree, [label, slug])

    def _on_add(self) -> None:
        dlg = _TypeEntryDialog(self, title=_("Add type preset"))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        label, slug = dlg.result_entry()
        if label and slug:
            self._append_row(label, slug)

    def _on_edit(self) -> None:
        item = self._tree.currentItem()
        if item is None:
            return
        dlg = _TypeEntryDialog(
            self,
            title=_("Edit type preset"),
            label=item.text(0),
            slug=item.text(1),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        label, slug = dlg.result_entry()
        if label and slug:
            item.setText(0, label)
            item.setText(1, slug)

    def _on_remove(self) -> None:
        item = self._tree.currentItem()
        if item is not None:
            idx = self._tree.indexOfTopLevelItem(item)
            self._tree.takeTopLevelItem(idx)

    def _on_restore(self) -> None:
        self._tree.clear()
        for label, slug in _default_type_options():
            self._append_row(label, slug)
        self._tree.clearSelection()
        self._tree.setCurrentItem(None)
        self._update_buttons(False)

    # ------------------------------------------------------------------

    def result_options(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            label = item.text(0).strip()
            slug  = item.text(1).strip().lower()
            if label and slug:
                out.append((label, slug))
        return out


# ---------------------------------------------------------------------------
# External applications
# ---------------------------------------------------------------------------

class ExternalApplicationsDialog(QDialog):
    """Per-scheme command template editor + global custom command."""

    def __init__(
        self,
        parent,
        *,
        initial_templates: dict[str, str],
        initial_custom: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("External applications"))
        self.resize(620, 360)

        intro = QLabel(
            _("Command templates for opening device connections. Leave empty to use the system default.")
        )
        intro.setWordWrap(True)

        defaults = default_connect_command_templates()
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self._entries: dict[str, QLineEdit] = {}
        for i, (scheme, title) in enumerate(_SCHEME_ROWS):
            lbl = QLabel(title + ":")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            ent = QLineEdit(initial_templates.get(scheme, ""))
            ent.setPlaceholderText(_("Empty = system default"))
            reset_btn = QPushButton(_("Reset"))
            reset_btn.setFixedWidth(64)
            default_val = defaults.get(scheme, "")
            reset_btn.clicked.connect(
                lambda _checked=False, e=ent, v=default_val: e.setText(v)
            )
            grid.addWidget(lbl,       i, 0)
            grid.addWidget(ent,       i, 1)
            grid.addWidget(reset_btn, i, 2)
            self._entries[scheme] = ent

        custom_row = len(_SCHEME_ROWS)
        custom_lbl = QLabel(_("Custom:"))
        custom_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._custom_edit = QLineEdit(initial_custom)
        self._custom_edit.setPlaceholderText(_("Empty = disabled"))
        clear_btn = QPushButton(_("Clear"))
        clear_btn.setFixedWidth(64)
        clear_btn.clicked.connect(lambda: self._custom_edit.clear())
        grid.addWidget(custom_lbl,      custom_row, 0)
        grid.addWidget(self._custom_edit, custom_row, 1)
        grid.addWidget(clear_btn,       custom_row, 2)

        hint = QLabel(
            "  ".join([
                "<b>{url}</b> " + _("full address"),
                "<b>{ip}</b> " + _("device IP"),
                "<b>{port}</b> " + _("service port"),
                "<b>{name}</b> " + _("device name"),
                "<b>{type}</b> " + _("device type"),
            ])
        )
        hint.setTextFormat(Qt.TextFormat.RichText)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: smaller;")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addSpacing(4)
        layout.addLayout(grid)
        layout.addWidget(hint)
        layout.addStretch(1)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------

    def result_templates(self) -> dict[str, str]:
        return {scheme: ent.text().strip() for scheme, ent in self._entries.items()}

    def result_custom(self) -> str:
        return self._custom_edit.text().strip()
