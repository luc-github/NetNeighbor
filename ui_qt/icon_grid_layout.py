# File icon_grid_layout.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Adaptive cell sizing for icon-mode ``QListWidget`` grids (flat Unsorted + grouped sections)."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QListWidget

_LOG = logging.getLogger(__name__)

# Must match ``ICON_MODE_LIST_QSS`` (1px border + 2px padding on each side).
_ITEM_BORDER_PX = 1
_ITEM_PADDING_PX = 2
_ITEM_INSET_LR = 2 * (_ITEM_BORDER_PX + _ITEM_PADDING_PX)
_ITEM_EXTRA_PAD = 4

_MAX_LABEL_LINES = 5

# Shared by flat icon list and Type / Location section lists.
LONG_NAME_WRAP_MIN_WIDTH_FACTOR = 3

# Total horizontal margin per tile: horizontal_pad×2 (20) + border+padding insets (6).
TILE_H_MARGIN = 26


def break_label_for_width(label: str, fm: QFontMetrics, max_px: int) -> str:
    """Insert \\n in words wider than max_px so Qt wraps them as explicit line breaks."""
    out: list[str] = []
    for line in label.split("\n"):
        words = line.split(" ")
        new_words: list[str] = []
        for word in words:
            if fm.horizontalAdvance(word) > max_px:
                new_w = ""
                cur_px = 0
                for ch in word:
                    ch_px = fm.horizontalAdvance(ch)
                    if cur_px + ch_px > max_px and cur_px > 0:
                        new_w += "\n"
                        cur_px = 0
                    new_w += ch
                    cur_px += ch_px
                word = new_w
            new_words.append(word)
        out.append(" ".join(new_words))
    return "\n".join(out)


def format_icon_tile_label(name: str) -> str:
    """Break long SSDP-style titles at `` - `` so each segment fits narrow icon cells."""
    n = (name or "").strip()
    if len(n) < 16 or " - " not in n:
        return n
    return n.replace(" - ", "\n")


def _label_max_horizontal_advance(fm: QFontMetrics, lbl: str) -> int:
    lines = (lbl or "").split("\n") or ["—"]
    return max(fm.horizontalAdvance(line) if line.strip() else fm.horizontalAdvance("—") for line in lines)


def icon_mode_label_font(list_widget: QListWidget) -> QFont:
    """Match ``font-size: smaller`` from ``ICON_MODE_LIST_QSS`` when measuring labels."""
    font = QFont(list_widget.font())
    pt = font.pointSizeF()
    if pt > 0:
        font.setPointSizeF(max(1.0, pt * 0.85))
    return font


def _text_block_height(
    fm: QFontMetrics,
    labels: list[str],
    inner_width: int,
    *,
    use_wrap: bool,
) -> int:
    inner_w = max(32, inner_width - _ITEM_INSET_LR)
    max_h = fm.height()
    # TextSingleLine strips explicit \n characters, underestimating height for labels
    # pre-formatted by format_icon_tile_label.  Use TextWordWrap whenever any label
    # contains a newline so Qt's boundingRect measures all rendered lines.
    any_newline = any("\n" in lbl for lbl in (labels or []))
    flags = Qt.TextFlag.TextWordWrap if (use_wrap or any_newline) else Qt.TextFlag.TextSingleLine
    cap_h = fm.lineSpacing() * _MAX_LABEL_LINES if (use_wrap or any_newline) else 0
    for lbl in labels or ["—"]:
        rect = fm.boundingRect(0, 0, inner_w, 10_000, flags, lbl)
        h = rect.height()
        if cap_h > 0:
            h = min(h, cap_h)
        max_h = max(max_h, h)
    return max_h


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
) -> tuple[QSize, bool]:
    """Return ``(grid cell size, use_word_wrap)`` for an icon-mode list.

    Uses the longest label width when there is room. When labels are wider than the
    minimum tile, cell width is at least ``3 ×`` that minimum (flat list and grouped
    sections). Word wrap with up to four lines is used when the cell is still narrower
    than the longest name.  ``max_cell_width`` caps the final tile width (respecting the
    icon-driven minimum so the icon always fits).
    """
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
        return (
            QSize(min_w, ih + label_gap + fm.height() + horizontal_pad + _ITEM_INSET_LR + _ITEM_EXTRA_PAD),
            False,
        )

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

    cell_w_before_long = cell_w
    if has_long_names:
        cell_w = max(cell_w, long_name_min_w)

    cell_w_before_cap = cell_w
    if max_cell_width is not None:
        cell_w = max(min_w, max_cell_width)  # fixed target width, not just a ceiling

    _LOG.debug(
        "cell_size icon=%dpx vp=%d count=%d text_w=%d min_w=%d "
        "computed=%d long_name=%d cap=%s → cell_w=%d text_zone=%d",
        iw, viewport_width, count, text_w, min_w,
        cell_w_before_long, cell_w_before_cap,
        str(max_cell_width),
        cell_w, max(0, cell_w - TILE_H_MARGIN),
    )

    use_wrap = True  # always word-wrap so Qt renders explicit \n characters correctly
    # Measure height from broken labels so forced \n added by break_label_for_width
    # (called by callers after relayout) are accounted for in cell height.
    text_zone_w = max(30, cell_w - TILE_H_MARGIN)
    broken_for_height = [break_label_for_width(lbl, fm, text_zone_w) for lbl in (labels or ["—"])]
    text_h = _text_block_height(
        fm,
        broken_for_height,
        cell_w - horizontal_pad * 2,
        use_wrap=use_wrap,
    )
    # Always reserve space for _MAX_LABEL_LINES regardless of actual label content so text
    # area stays consistent across all icon size presets and short names don't collapse the cell.
    text_h = max(text_h, fm.lineSpacing() * _MAX_LABEL_LINES)
    cell_h = ih + label_gap + text_h + horizontal_pad + _ITEM_INSET_LR + _ITEM_EXTRA_PAD

    return QSize(cell_w, cell_h), use_wrap


def icon_list_spacing_for_cell(icon_size: QSize) -> int:
    """Inter-tile spacing scaled with icon preset."""
    h = max(1, icon_size.height())
    return max(8, min(36, h // 4))


def icon_list_content_height(
    list_widget: QListWidget,
    grid_cell: QSize,
    *,
    item_count: int | None = None,
    viewport_width: int | None = None,
) -> int:
    """Pixel height for an icon-mode list grid (grouped sections — not full viewport)."""
    count = item_count if item_count is not None else list_widget.count()
    if count <= 0:
        return 0
    vp_w = viewport_width if (viewport_width is not None and viewport_width > 0) else max(1, list_widget.viewport().width())
    cw = max(1, grid_cell.width())
    ch = max(1, grid_cell.height())
    spacing = list_widget.spacing()
    cols = max(1, (vp_w + spacing) // (cw + spacing))
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
    """Apply spacing, grid size, and word-wrap for an icon-mode list."""
    list_widget.setSpacing(icon_list_spacing_for_cell(icon_size))
    list_widget.setIconSize(icon_size)

    vp_w = list_widget.viewport().width()
    if vp_w <= 0 and fallback_viewport_width > 0:
        vp_w = fallback_viewport_width

    iw = icon_size.width()
    # Fixed max tile widths: 3×48=144 for small/medium/large, icon-driven for xlarge (text≈250px).
    if iw >= 256:
        max_cw = iw + 20  # 276px → text zone = 276 - 26 = 250px
    else:
        max_cw = 3 * 48   # 144px for small/medium/large

    _LOG.debug("apply_layout icon=%dpx vp=%d max_cw=%d labels=%d", iw, vp_w, max_cw, len(labels))

    cell, use_wrap = compute_icon_mode_cell_size(
        vp_w,
        icon_size,
        labels,
        icon_mode_label_font(list_widget),
        list_spacing=list_widget.spacing(),
        max_cell_width=max_cw,
    )
    list_widget.setWordWrap(use_wrap)
    list_widget.setGridSize(cell)
    list_widget.setMinimumHeight(0)
    list_widget.setMaximumHeight(16777215)
    list_widget.doItemsLayout()
    if compact_height:
        content_h = icon_list_content_height(list_widget, cell, viewport_width=vp_w)
        if content_h > 0:
            list_widget.setFixedHeight(content_h)
    elif set_min_height:
        ih = icon_size.height()
        list_widget.setMinimumHeight(max(96, cell.height(), ih + 56))


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
