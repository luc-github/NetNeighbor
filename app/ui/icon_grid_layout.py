# File icon_grid_layout.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Adaptive cell sizing for icon-mode ``QListWidget`` grids (flat + grouped sections)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QIcon
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from ui.icon_tile_label import (
    ICON_TILE_LABEL_ROLE,
    LONG_NAME_WRAP_MIN_WIDTH_FACTOR,
    MIN_LINES_BEFORE_ELLIPSIS,
    TILE_H_MARGIN,
    _ITEM_EXTRA_PAD,
    _ITEM_INSET_LR,
    _label_max_horizontal_advance,
    format_icon_tile_label,
    icon_mode_label_font,
    icon_tile_cell_height,
    icon_tile_label_paint_rect,
    max_label_block_height_for_labels,
    normalize_icon_tile_label,
    refresh_icon_tile_display_cache,
)

_LOG = logging.getLogger(__name__)

# Re-export for callers that imported these from icon_grid_layout.
__all__ = [
    "ICON_TILE_LABEL_ROLE",
    "TILE_H_MARGIN",
    "format_icon_tile_label",
    "normalize_icon_tile_label",
    "set_icon_tile_item_label",
    "collect_icon_tile_labels",
    "icon_mode_label_font",
    "relayout_icon_mode_list",
    "apply_icon_mode_list_layout",
    "IconListViewportResizeFilter",
    "icon_list_spacing_for_cell",
    "icon_list_content_height",
    "labels_from_icon_list",
]


def create_icon_tile_list_item(icon: QIcon, name: str) -> QListWidgetItem:
    """``QListWidgetItem`` requires ``(icon, text)`` in PySide6 — never pass icon alone."""
    item = QListWidgetItem(icon, "")
    set_icon_tile_item_label(item, name)
    return item


def set_icon_tile_item_label(item: QListWidgetItem, name: str) -> str:
    """Store the unbroken label on *item*; returns the normalized string."""
    label = normalize_icon_tile_label(name)
    item.setData(ICON_TILE_LABEL_ROLE, label)
    item.setText(label)
    return label


def collect_icon_tile_labels(list_widget: QListWidget) -> list[str]:
    out: list[str] = []
    for i in range(list_widget.count()):
        it = list_widget.item(i)
        if it is None:
            continue
        stored = it.data(ICON_TILE_LABEL_ROLE)
        if isinstance(stored, str) and stored:
            out.append(stored)
        else:
            out.append(normalize_icon_tile_label(it.text()))
    return out


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
    max_cell_width: int | None = None,
) -> QSize:
    """Return grid cell size; label height from ``QTextLayout`` (min four lines)."""
    iw = max(1, icon_size.width())
    ih = max(1, icon_size.height())
    min_w = min_cell_width if min_cell_width is not None else iw + 20
    long_name_min_w = min_w * LONG_NAME_WRAP_MIN_WIDTH_FACTOR

    fm = QFontMetrics(font)
    if labels:
        text_w = max(_label_max_horizontal_advance(fm, lbl) for lbl in labels)
    else:
        text_w = fm.horizontalAdvance("—")

    has_long_names = text_w > min_w
    text_inner_min = text_w + horizontal_pad * 2 + _ITEM_INSET_LR
    max_cell_w = max(min_w, iw + horizontal_pad * 2 + _ITEM_INSET_LR, text_inner_min)

    count = len(labels)
    if count <= 0:
        dummy = QRect(0, 0, min_w, 10000)
        empty_h = max_label_block_height_for_labels([], font, icon_tile_label_paint_rect(dummy, ih).width())
        return QSize(min_w, icon_tile_cell_height(ih, empty_h))

    vp = max(min_w, viewport_width)

    def _fits_one_row(cell_w: int) -> bool:
        # Qt adds list_spacing on all 4 viewport sides: (count+1) gaps total.
        return count * cell_w + (count + 1) * list_spacing <= vp

    if _fits_one_row(max_cell_w):
        cell_w = max_cell_w
    elif count == 1:
        cell_w = min(max_cell_w, vp)
    else:
        cell_w = max(min_w, (vp - (count + 1) * list_spacing) // count)
        cell_w = min(cell_w, max_cell_w)
        if not _fits_one_row(cell_w):
            cols = max(1, (vp - list_spacing) // (min_w + list_spacing))
            cell_w = max(min_w, (vp - list_spacing * (cols + 1)) // cols)
            cell_w = min(cell_w, max_cell_w)

    if has_long_names:
        cell_w = max(cell_w, long_name_min_w)

    if max_cell_width is not None:
        cell_w = max(min_w, max_cell_width)

    dummy_cell = QRect(0, 0, cell_w, 10000)
    zone_w = icon_tile_label_paint_rect(dummy_cell, ih).width()
    max_text_h = max_label_block_height_for_labels(labels, font, zone_w)
    cell_h = icon_tile_cell_height(ih, max_text_h)

    _LOG.debug(
        "cell_size icon=%dpx vp=%d count=%d cell_w=%d zone=%d text_h=%d cell_h=%d",
        iw, viewport_width, count, cell_w, zone_w, max_text_h, cell_h,
    )
    return QSize(cell_w, cell_h)


def icon_list_spacing_for_cell(icon_size: QSize) -> int:
    h = max(1, icon_size.height())
    return max(4, min(12, h // 8))


def icon_list_content_height(
    list_widget: QListWidget,
    grid_cell: QSize,
    *,
    item_count: int | None = None,
    viewport_width: int | None = None,
) -> int:
    count = item_count if item_count is not None else list_widget.count()
    if count <= 0:
        return 0
    vp_w = viewport_width if (viewport_width is not None and viewport_width > 0) else max(1, list_widget.viewport().width())
    cw = max(1, grid_cell.width())
    ch = max(1, grid_cell.height())
    spacing = list_widget.spacing()
    cols = max(1, (vp_w - spacing) // (cw + spacing))
    rows = (count + cols - 1) // cols
    return rows * ch + (rows + 1) * spacing


def apply_icon_mode_list_layout(
    list_widget: QListWidget,
    icon_size: QSize,
    labels: list[str],
    *,
    set_min_height: bool = False,
    compact_height: bool = False,
    fallback_viewport_width: int = 0,
) -> None:
    list_widget.setSpacing(icon_list_spacing_for_cell(icon_size))
    list_widget.setIconSize(icon_size)

    vp_w = list_widget.viewport().width()
    if vp_w <= 0 and fallback_viewport_width > 0:
        vp_w = fallback_viewport_width

    iw = icon_size.width()
    if iw >= 256:
        max_cw = iw + 20
    elif iw >= 96:
        max_cw = iw + 96
    else:
        max_cw = 3 * 48

    cell = compute_icon_mode_cell_size(
        vp_w,
        icon_size,
        labels,
        icon_mode_label_font(list_widget),
        list_spacing=list_widget.spacing(),
        max_cell_width=max_cw,
    )
    list_widget.setWordWrap(False)
    list_widget.setTextElideMode(Qt.TextElideMode.ElideNone)
    list_widget.setGridSize(cell)
    list_widget.setMinimumHeight(0)
    list_widget.setMaximumHeight(16777215)
    list_widget.doItemsLayout()
    list_widget.updateGeometries()
    if compact_height:
        content_h = icon_list_content_height(list_widget, cell, viewport_width=vp_w)
        if content_h > 0:
            list_widget.setFixedHeight(content_h)
    elif set_min_height:
        ih = icon_size.height()
        list_widget.setMinimumHeight(max(96, cell.height(), ih + 56))


def relayout_icon_mode_list(
    list_widget: QListWidget,
    icon_size: QSize,
    *,
    labels: Sequence[str] | None = None,
    compact_height: bool = False,
    set_min_height: bool = False,
    fallback_viewport_width: int = 0,
    force: bool = False,
) -> None:
    """Resize grid cells; precompute wrapped lines once (delegate reads cache)."""
    label_list = list(labels) if labels is not None else collect_icon_tile_labels(list_widget)
    if not label_list and list_widget.count() <= 0:
        return

    vp_w = list_widget.viewport().width()
    if vp_w <= 0 and fallback_viewport_width > 0:
        vp_w = fallback_viewport_width
    layout_key = (vp_w, icon_size.width(), icon_size.height(), len(label_list))
    if not force and getattr(list_widget, "_nn_icon_layout_key", None) == layout_key:
        return
    list_widget._nn_icon_layout_key = layout_key  # type: ignore[attr-defined]

    apply_icon_mode_list_layout(
        list_widget,
        icon_size,
        label_list,
        compact_height=compact_height,
        set_min_height=set_min_height,
        fallback_viewport_width=fallback_viewport_width,
    )
    refresh_icon_tile_display_cache(list_widget, icon_size, label_list)
    for i, orig in enumerate(label_list):
        it = list_widget.item(i)
        if it is None:
            continue
        if it.data(ICON_TILE_LABEL_ROLE) != orig:
            it.setData(ICON_TILE_LABEL_ROLE, orig)
        if it.text() != orig:
            it.setText(orig)


class IconListViewportResizeFilter(QObject):
    """Debounce resize storms (each pixel was relayouting every label)."""

    def __init__(
        self,
        on_resize: Callable[[], None],
        parent: QObject | None = None,
        *,
        debounce_ms: int = 80,
    ) -> None:
        super().__init__(parent)
        self._on_resize = on_resize
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(0, debounce_ms))
        self._timer.timeout.connect(self._on_resize)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self._timer.start()
        return False


def labels_from_icon_list(list_widget: QListWidget) -> list[str]:
    return collect_icon_tile_labels(list_widget)
