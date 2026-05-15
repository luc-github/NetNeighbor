# File no_focus_item_delegate.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Item delegate that skips the platform focus frame (inner border on selected cells)."""

from __future__ import annotations

from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem


class NoFocusItemDelegate(QStyledItemDelegate):
    """Paint like the default delegate but without ``State_HasFocus`` (no focus rectangle)."""

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, opt, index)
