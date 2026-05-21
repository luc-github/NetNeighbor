# File icon_tile_delegate.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Delegate for icon-mode tiles: cached Qt text layout, no focus ring."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QListWidget,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
)

from ui.icon_tile_label import (
    ICON_TILE_DISPLAY_HEIGHTS_ROLE,
    ICON_TILE_DISPLAY_LINES_ROLE,
    ICON_TILE_LABEL_ROLE,
    display_lines_for_tile,
    icon_mode_label_font,
    icon_tile_cell_rect,
    icon_tile_icon_rect,
    icon_tile_label_paint_rect,
    layout_icon_tile_label,
)


class IconTileItemDelegate(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # type: ignore[override]
        widget = option.widget
        if isinstance(widget, QListWidget):
            grid = widget.gridSize()
            if grid.width() > 0 and grid.height() > 0:
                return QSize(grid)
        return super().sizeHint(option, index)

    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:  # type: ignore[override]
        super().initStyleOption(option, index)
        option.textElideMode = Qt.TextElideMode.ElideNone
        option.features &= ~QStyleOptionViewItem.ViewItemFeature.WrapText
        option.text = ""
        list_widget = option.widget
        if isinstance(list_widget, QListWidget):
            option.rect = icon_tile_cell_rect(list_widget, option.rect)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        list_widget = opt.widget if isinstance(opt.widget, QListWidget) else None
        if list_widget is not None:
            opt.rect = list_widget.visualRect(index)

        opt.state &= ~QStyle.StateFlag.State_HasFocus
        opt.state &= ~QStyle.StateFlag.State_Selected

        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, widget)

        cell = icon_tile_cell_rect(list_widget, opt.rect)
        icon_size = list_widget.iconSize() if list_widget is not None else QSize(48, 48)

        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon is not None and isinstance(icon, QIcon) and not icon.isNull():
            icon.paint(painter, icon_tile_icon_rect(cell, icon_size), Qt.AlignmentFlag.AlignCenter)

        label = index.data(ICON_TILE_LABEL_ROLE)
        if not isinstance(label, str) or not label.strip():
            label = index.data(Qt.ItemDataRole.DisplayRole)
        if not isinstance(label, str) or not label.strip():
            return

        font = icon_mode_label_font(list_widget) if list_widget is not None else opt.font
        painter.save()
        painter.setFont(font)

        text_rect = icon_tile_label_paint_rect(cell, icon_size.height())
        text_w = max(8, text_rect.width())

        lines_data = index.data(ICON_TILE_DISPLAY_LINES_ROLE)
        heights_data = index.data(ICON_TILE_DISPLAY_HEIGHTS_ROLE)
        if isinstance(lines_data, (list, tuple)) and lines_data:
            lines = tuple(str(x) for x in lines_data)
            heights = (
                tuple(int(x) for x in heights_data)
                if isinstance(heights_data, (list, tuple)) and len(heights_data) == len(lines)
                else None
            )
        else:
            laid_out = layout_icon_tile_label(label, font, text_w)
            lines = display_lines_for_tile(laid_out, font=font, text_width_px=text_w)
            heights = laid_out.line_heights

        fm = QFontMetrics(font)
        default_h = fm.lineSpacing()
        color = opt.palette.color(
            opt.palette.currentColorGroup(),
            opt.palette.ColorRole.Text,
        )
        painter.setPen(QColor(color))

        y = text_rect.top()
        for i, line in enumerate(lines):
            line_h = (
                max(1, int(heights[i]))
                if heights is not None and i < len(heights)
                else default_h
            )
            painter.drawText(
                QRect(text_rect.left(), y, text_rect.width(), line_h),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                line,
            )
            y += line_h

        painter.restore()
