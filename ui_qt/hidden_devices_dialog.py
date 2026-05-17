# File hidden_devices_dialog.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Dialog listing user-hidden devices with per-row remove actions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from gettext import gettext as _

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True, slots=True)
class HiddenDeviceRow:
    host_key: str
    name: str
    ip: str
    port: int


class HiddenDevicesDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        rows: Sequence[HiddenDeviceRow],
        on_remove: Callable[[str], None],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Hidden devices"))
        self.setModal(True)
        self.resize(640, 360)
        self._on_remove = on_remove
        self._row_keys: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels([_("Device"), _("IP"), ""])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)

        for row in rows:
            self._append_row(row)

        if not rows:
            self._show_empty_placeholder()

        layout.addWidget(self._table)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.reject)
        close_btn = close_box.button(QDialogButtonBox.StandardButton.Close)
        if close_btn:
            close_btn.setText(_("Close"))
        layout.addWidget(close_box)

    def _append_row(self, row: HiddenDeviceRow) -> None:
        if self._table.rowCount() == 1 and self._table.cellWidget(0, 2) is None:
            self._table.setRowCount(0)
            self._row_keys.clear()
        r = self._table.rowCount()
        self._table.insertRow(r)
        self._row_keys.append(row.host_key)
        self._table.setItem(r, 0, QTableWidgetItem(row.name))
        ip_item = QTableWidgetItem(row.ip)
        ip_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(r, 1, ip_item)
        remove_btn = QPushButton(_("Remove"))
        remove_btn.clicked.connect(lambda _checked=False, key=row.host_key: self._remove_row(key))
        self._table.setCellWidget(r, 2, remove_btn)

    def _show_empty_placeholder(self) -> None:
        self._table.setRowCount(1)
        self._row_keys.clear()
        empty = QTableWidgetItem(_("No hidden devices"))
        empty.setFlags(Qt.ItemFlag.NoItemFlags)
        self._table.setItem(0, 0, empty)
        self._table.setSpan(0, 0, 1, 3)

    def _remove_row(self, host_key: str) -> None:
        self._on_remove(host_key)
        for r, key in enumerate(list(self._row_keys)):
            if key == host_key:
                self._table.removeRow(r)
                self._row_keys.pop(r)
                break
        if not self._row_keys:
            self._show_empty_placeholder()


def show_hidden_devices_dialog(
    parent: QWidget | None,
    rows: Sequence[HiddenDeviceRow],
    on_remove: Callable[[str], None],
) -> None:
    HiddenDevicesDialog(parent, rows, on_remove).exec()
