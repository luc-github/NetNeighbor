# File icon_tile_label.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Qt-native wrapping and measurement for icon-tile device names."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics, QTextLayout, QTextOption
from PySide6.QtWidgets import QListWidget

# No ellipsis while the wrapped label fits on this many lines or fewer.
MIN_LINES_BEFORE_ELLIPSIS = 4

# Shared tile geometry (measurement + delegate paint must match).
ICON_TILE_TOP_PAD = 4
ICON_TILE_ICON_LABEL_GAP = 6
ICON_TILE_SIDE_PAD = 4
ICON_TILE_BOTTOM_PAD = 4

# Must match ``ICON_MODE_LIST_QSS`` (1px border + 2px padding on each side).
_ITEM_BORDER_PX = 1
_ITEM_PADDING_PX = 2
_ITEM_INSET_LR = 2 * (_ITEM_BORDER_PX + _ITEM_PADDING_PX)
_ITEM_EXTRA_PAD = 4

ICON_TILE_LABEL_ROLE = Qt.ItemDataRole.UserRole + 2
ICON_TILE_DISPLAY_LINES_ROLE = Qt.ItemDataRole.UserRole + 3
ICON_TILE_DISPLAY_HEIGHTS_ROLE = Qt.ItemDataRole.UserRole + 4

LONG_NAME_WRAP_MIN_WIDTH_FACTOR = 3
# Legacy alias: horizontal text inset is ``2 * ICON_TILE_SIDE_PAD``.
TILE_H_MARGIN = 2 * ICON_TILE_SIDE_PAD + _ITEM_INSET_LR


@dataclass(frozen=True, slots=True)
class IconTileLabelLayout:
    lines: tuple[str, ...]
    line_heights: tuple[int, ...]
    total_height: int

    @property
    def line_count(self) -> int:
        return len(self.lines)


def normalize_icon_tile_label(name: str) -> str:
    return (name or "").strip() or "—"


def format_icon_tile_label(name: str) -> str:
    return normalize_icon_tile_label(name)


def icon_mode_label_font(list_widget: QListWidget) -> QFont:
    font = QFont(list_widget.font())
    pt = font.pointSizeF()
    if pt > 0:
        font.setPointSizeF(max(1.0, pt * 0.85))
    return font


def icon_tile_text_zone_width(cell_width: int) -> int:
    return max(30, cell_width - 2 * ICON_TILE_SIDE_PAD)


def icon_tile_text_top(icon_height: int) -> int:
    return ICON_TILE_TOP_PAD + max(1, icon_height) + ICON_TILE_ICON_LABEL_GAP


def icon_tile_cell_rect(list_widget: QListWidget | None, item_rect: QRect) -> QRect:
    cell = QRect(item_rect)
    if list_widget is not None:
        grid = list_widget.gridSize()
        if grid.width() > 0:
            cell.setWidth(grid.width())
        if grid.height() > 0:
            cell.setHeight(grid.height())
    return cell


def icon_tile_icon_rect(cell: QRect, icon_size: QSize) -> QRect:
    """Icon anchored at top-centre of the tile (not vertically centred in the cell)."""
    iw = max(1, icon_size.width())
    ih = max(1, icon_size.height())
    x = cell.left() + max(0, (cell.width() - iw) // 2)
    y = cell.top() + ICON_TILE_TOP_PAD
    return QRect(x, y, iw, ih)


def icon_tile_label_paint_rect(cell: QRect, icon_height: int) -> QRect:
    """Text block below the icon, relative to *cell* coordinates."""
    top = cell.top() + icon_tile_text_top(icon_height)
    return QRect(
        cell.left() + ICON_TILE_SIDE_PAD,
        top,
        max(30, cell.width() - 2 * ICON_TILE_SIDE_PAD),
        max(1, cell.bottom() - top - ICON_TILE_BOTTOM_PAD + 1),
    )


def layout_icon_tile_label(text: str, font: QFont, text_width_px: int) -> IconTileLabelLayout:
    if text_width_px <= 0:
        fm = QFontMetrics(font)
        return IconTileLabelLayout((text or "—",), (fm.lineSpacing(),), fm.lineSpacing())

    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)

    layout = QTextLayout(text, font)
    layout.setTextOption(option)
    layout.beginLayout()

    lines: list[str] = []
    heights: list[int] = []
    total = 0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(float(text_width_px))
        start = line.textStart()
        length = line.textLength()
        lines.append(text[start : start + length])
        h = max(1, int(line.height()))
        heights.append(h)
        total += h

    layout.endLayout()

    if not lines:
        fm = QFontMetrics(font)
        h = fm.lineSpacing()
        return IconTileLabelLayout((text or "—",), (h,), h)

    return IconTileLabelLayout(tuple(lines), tuple(heights), total)


def display_lines_for_tile(
    laid_out: IconTileLabelLayout,
    *,
    font: QFont,
    text_width_px: int,
) -> tuple[str, ...]:
    lines = laid_out.lines
    if len(lines) <= MIN_LINES_BEFORE_ELLIPSIS:
        return lines

    fm = QFontMetrics(font)
    tail = " ".join(lines[MIN_LINES_BEFORE_ELLIPSIS:])
    elided = fm.elidedText(tail, Qt.TextElideMode.ElideRight, text_width_px)
    return (*lines[:MIN_LINES_BEFORE_ELLIPSIS], elided)


def display_block_height(
    display_lines: Sequence[str],
    line_heights: Sequence[int],
    font: QFont,
) -> int:
    """Pixel height of the lines actually shown (no empty 4-line floor)."""
    if line_heights and len(line_heights) >= len(display_lines):
        return sum(int(line_heights[i]) for i in range(len(display_lines)))
    fm = QFontMetrics(font)
    return len(display_lines) * fm.lineSpacing()


def icon_tile_cell_height(icon_height: int, label_block_height: int) -> int:
    return (
        icon_tile_text_top(icon_height)
        + label_block_height
        + ICON_TILE_BOTTOM_PAD
        + _ITEM_INSET_LR
        + _ITEM_EXTRA_PAD
    )


def _label_max_horizontal_advance(fm: QFontMetrics, lbl: str) -> int:
    return fm.horizontalAdvance(lbl or "—")


def refresh_icon_tile_display_cache(
    list_widget: QListWidget,
    icon_size: QSize,
    labels: Sequence[str],
) -> None:
    cell_w = list_widget.gridSize().width()
    if cell_w <= 0:
        return
    if not isinstance(icon_size, QSize):
        icon_size = QSize(icon_size)
    font = icon_mode_label_font(list_widget)
    icon_h = max(1, icon_size.height())
    dummy_cell = QRect(0, 0, cell_w, 10000)
    text_w = icon_tile_label_paint_rect(dummy_cell, icon_h).width()
    fm = QFontMetrics(font)
    default_h = fm.lineSpacing()
    for i, orig in enumerate(labels):
        it = list_widget.item(i)
        if it is None:
            continue
        laid_out = layout_icon_tile_label(orig, font, text_w)
        display = display_lines_for_tile(laid_out, font=font, text_width_px=text_w)
        heights = tuple(
            laid_out.line_heights[j] if j < len(laid_out.line_heights) else default_h
            for j in range(len(display))
        )
        if len(display) > MIN_LINES_BEFORE_ELLIPSIS and len(heights) == len(display):
            heights = (*heights[:-1], default_h)
        it.setData(ICON_TILE_DISPLAY_LINES_ROLE, display)
        it.setData(ICON_TILE_DISPLAY_HEIGHTS_ROLE, heights)


def max_label_block_height_for_labels(
    labels: Sequence[str],
    font: QFont,
    text_width_px: int,
) -> int:
    fm = QFontMetrics(font)
    if not labels:
        return fm.lineSpacing()
    max_h = 0
    for lbl in labels:
        laid_out = layout_icon_tile_label(lbl, font, text_width_px)
        display = display_lines_for_tile(laid_out, font=font, text_width_px=text_width_px)
        heights = tuple(
            laid_out.line_heights[j] if j < len(laid_out.line_heights) else QFontMetrics(font).lineSpacing()
            for j in range(len(display))
        )
        if len(display) > MIN_LINES_BEFORE_ELLIPSIS:
            heights = (*heights[:-1], QFontMetrics(font).lineSpacing())
        max_h = max(max_h, display_block_height(display, heights, font))
    return max_h
