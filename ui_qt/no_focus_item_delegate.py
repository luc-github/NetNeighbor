# File no_focus_item_delegate.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Item delegate that skips the platform focus frame (inner border on selected cells)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidget, QStyledItemDelegate, QStyle, QStyleOptionViewItem


class NoFocusItemDelegate(QStyledItemDelegate):
    """Paint like the default delegate but without ``State_HasFocus`` (no focus rectangle)."""

    def __init__(self, parent=None, *, elide_none: bool = False) -> None:
        super().__init__(parent)
        self._elide_none = elide_none

    def initStyleOption(self, option: QStyleOptionViewItem, index):  # type: ignore[override]
        super().initStyleOption(option, index)
        # super() calls view->initViewItemOption() which resets textElideMode to ElideRight
        # and resets features based on QListWidget.wordWrap().  Set our overrides AFTER.
        w = self.parent()
        if isinstance(w, QListWidget):
            # WrapText must be set for Qt to respect explicit \n characters in item text.
            # Without it QCommonStyle adds TextSingleLine which strips newlines, collapsing
            # relayout_icon_mode_list's explicit \\n breaks into a single clipped line.
            text = index.data(Qt.ItemDataRole.DisplayRole)
            has_newlines = isinstance(text, str) and "\n" in text
            if w.wordWrap() or has_newlines:
                option.features |= QStyleOptionViewItem.ViewItemFeature.WrapText
        if self._elide_none:
            option.textElideMode = Qt.TextElideMode.ElideNone

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        opt.state &= ~QStyle.StateFlag.State_Selected
        # Call drawControl directly instead of super().paint() to avoid the
        # second initStyleOption() call inside QStyledItemDelegate::paint that
        # would re-read view state and overwrite our WrapText / ElideNone overrides.
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
