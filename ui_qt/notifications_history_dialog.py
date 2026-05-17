# File notifications_history_dialog.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Notifications history dialog — shows past online/offline transitions."""

from __future__ import annotations

from collections.abc import Callable
from gettext import gettext as _

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class NotificationsHistoryDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        log: list[tuple[str, str, str]],
        on_clear: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Notifications history"))
        self.setModal(True)
        self.resize(680, 420)

        self._on_clear = on_clear

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self._table = QTableWidget(len(log), 3)
        self._table.setHorizontalHeaderLabels(
            [_("Date / Time"), _("Device"), _("Status")]
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)

        for r, (dt, name, status) in enumerate(log):
            self._table.setItem(r, 0, QTableWidgetItem(dt))
            self._table.setItem(r, 1, QTableWidgetItem(name))
            status_item = QTableWidgetItem(status)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(r, 2, status_item)

        self._table.resizeColumnsToContents()
        self._table.clearSelection()
        self._table.setCurrentIndex(self._table.model().index(-1, -1))
        layout.addWidget(self._table)

        btn_bar = QHBoxLayout()
        clear_btn = QPushButton(_("Clear"))
        clear_btn.clicked.connect(self._do_clear)
        btn_bar.addWidget(clear_btn)
        btn_bar.addStretch(1)

        close_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_box.rejected.connect(self.reject)
        close_btn = close_box.button(QDialogButtonBox.StandardButton.Close)
        if close_btn:
            close_btn.setText(_("Close"))
        btn_bar.addWidget(close_box)

        layout.addLayout(btn_bar)

    def _do_clear(self) -> None:
        self._table.setRowCount(0)
        self._on_clear()


def show_notifications_history_dialog(
    parent: QWidget | None,
    log: list[tuple[str, str, str]],
    on_clear: Callable[[], None],
) -> None:
    NotificationsHistoryDialog(parent, log, on_clear).exec()
