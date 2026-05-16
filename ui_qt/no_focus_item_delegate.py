# File no_focus_item_delegate.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Item delegate that skips the platform focus frame (inner border on selected cells)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QStyledItemDelegate, QStyle, QStyleOptionViewItem


class NoFocusItemDelegate(QStyledItemDelegate):
    """Paint like the default delegate but without ``State_HasFocus`` (no focus rectangle)."""

    def __init__(self, parent=None, *, elide_none: bool = False) -> None:
        super().__init__(parent)
        self._elide_none = elide_none

    def initStyleOption(self, option: QStyleOptionViewItem, index):  # type: ignore[override]
        super().initStyleOption(option, index)
        w = self.parent()
        if isinstance(w, QListWidget) and w.wordWrap():
            option.features |= QStyleOptionViewItem.ViewItemFeature.WrapText

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        if self._elide_none:
            opt.textElideMode = Qt.TextElideMode.ElideNone
        super().paint(painter, opt, index)
