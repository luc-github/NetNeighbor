# File icon_grid_layout.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Adaptive cell sizing for icon-mode ``QListWidget`` grids (flat Unsorted + grouped sections)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QListWidget

# Shared by flat icon list and Type / Location section lists.
LONG_NAME_WRAP_MIN_WIDTH_FACTOR = 3
LONG_NAME_WRAP_MAX_LINES = 4
SHORT_WRAP_MAX_LINES = 2


def compute_icon_mode_cell_size(
    viewport_width: int,
    icon_size: QSize,
    labels: list[str],
    font: QFont,
    *,
    list_spacing: int = 10,
    horizontal_pad: int = 10,
    label_gap: int = 6,
    min_cell_width: int | None = None,
) -> tuple[QSize, bool]:
    """Return ``(grid cell size, use_word_wrap)`` for an icon-mode list.

    Uses the longest label width when there is room. When labels are wider than the
    minimum tile, cell width is at least ``3 ×`` that minimum (flat list and grouped
    sections). Word wrap with up to four lines is used when the cell is still narrower
    than the longest name.
    """
    iw = max(1, icon_size.width())
    ih = max(1, icon_size.height())
    min_w = min_cell_width if min_cell_width is not None else iw + 20
    long_name_min_w = min_w * LONG_NAME_WRAP_MIN_WIDTH_FACTOR

    fm = QFontMetrics(font)
    line_h = fm.height()
    if labels:
        text_w = max(fm.horizontalAdvance(lbl) for lbl in labels)
    else:
        text_w = fm.horizontalAdvance("—")

    has_long_names = text_w > min_w
    text_inner_min = text_w + horizontal_pad * 2

    max_cell_w = max(min_w, iw + horizontal_pad * 2, text_inner_min)
    compact_cell_h = ih + line_h + label_gap + horizontal_pad

    count = len(labels)
    if count <= 0:
        return QSize(min_w, compact_cell_h), False

    vp = max(min_w, viewport_width)
    gaps_one_row = list_spacing * max(0, count - 1)

    def _fits_one_row(cell_w: int) -> bool:
        return count * cell_w + gaps_one_row <= vp

    if _fits_one_row(max_cell_w):
        cell_w = max_cell_w
    elif count == 1:
        cell_w = min(max_cell_w, vp)
    else:
        cell_w = max(min_w, (vp - gaps_one_row) // count)
        cell_w = min(cell_w, max_cell_w)
        if not _fits_one_row(cell_w):
            cols = max(1, (vp + list_spacing) // (min_w + list_spacing))
            cell_w = max(min_w, (vp - list_spacing * (cols - 1)) // cols)
            cell_w = min(cell_w, max_cell_w)

    if has_long_names:
        cell_w = max(cell_w, long_name_min_w)

    use_wrap = cell_w < text_inner_min

    if use_wrap:
        wrap_lines = LONG_NAME_WRAP_MAX_LINES if has_long_names else SHORT_WRAP_MAX_LINES
        cell_h = ih + wrap_lines * line_h + label_gap + horizontal_pad
    else:
        cell_h = compact_cell_h

    return QSize(cell_w, cell_h), use_wrap


def icon_list_spacing_for_cell(icon_size: QSize) -> int:
    """Inter-tile spacing scaled with icon preset."""
    h = max(1, icon_size.height())
    return max(8, min(36, h // 4))


def apply_icon_mode_list_layout(
    list_widget: QListWidget,
    icon_size: QSize,
    labels: list[str],
    *,
    set_min_height: bool = False,
    fallback_viewport_width: int = 0,
) -> None:
    """Apply spacing, grid size, and word-wrap for an icon-mode list."""
    list_widget.setSpacing(icon_list_spacing_for_cell(icon_size))
    list_widget.setIconSize(icon_size)

    vp_w = list_widget.viewport().width()
    if vp_w <= 0 and fallback_viewport_width > 0:
        vp_w = fallback_viewport_width

    cell, use_wrap = compute_icon_mode_cell_size(
        vp_w,
        icon_size,
        labels,
        list_widget.font(),
        list_spacing=list_widget.spacing(),
    )
    list_widget.setWordWrap(use_wrap)
    list_widget.setGridSize(cell)
    if set_min_height:
        ih = icon_size.height()
        list_widget.setMinimumHeight(max(96, cell.height(), ih + 56))
    list_widget.doItemsLayout()


class IconListViewportResizeFilter(QObject):
    """Invoke ``on_resize`` when the watched widget is resized."""

    def __init__(self, on_resize: Callable[[], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._on_resize = on_resize

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self._on_resize()
        return False


def labels_from_icon_list(list_widget: QListWidget) -> list[str]:
    out: list[str] = []
    for i in range(list_widget.count()):
        it = list_widget.item(i)
        if it is None:
            continue
        text = (it.text() or "").strip()
        if text:
            out.append(text)
    return out
