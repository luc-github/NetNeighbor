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

_MAX_LABEL_LINES = 5   # cap: never measure beyond this many lines
_MIN_LABEL_LINES = 2   # floor: always reserve at least this many lines per cell

# Shared by flat icon list and Type / Location section lists.
LONG_NAME_WRAP_MIN_WIDTH_FACTOR = 3

# Total horizontal margin per tile: horizontal_pad×2 (20) + border+padding insets (6).
TILE_H_MARGIN = 26


def _split_word_at_hyphens(word: str, fm: QFontMetrics, max_px: int) -> list[str]:
    """Group hyphen-delimited parts of *word* into chunks that fit within *max_px*."""
    raw = word.split('-')
    if len(raw) <= 1:
        return [word]
    parts = [p + '-' for p in raw[:-1]] + [raw[-1]]
    tokens: list[str] = []
    current = ''
    for part in parts:
        test = current + part
        if fm.horizontalAdvance(test) <= max_px:
            current = test
        else:
            if current:
                tokens.append(current)
            current = part
    if current:
        tokens.append(current)
    return tokens or [word]


def _split_by_chars(text: str, fm: QFontMetrics, max_px: int) -> list[str]:
    """Split *text* character-by-character into chunks fitting *max_px*."""
    lines: list[str] = []
    current = ''
    for ch in text:
        test = current + ch
        if fm.horizontalAdvance(test) <= max_px:
            current = test
        else:
            if current:
                lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return lines or ['']


def _wrap_one_segment(text: str, fm: QFontMetrics, max_px: int) -> list[str]:
    """Wrap a single text segment (no \\n) into lines of at most *max_px* pixels.

    Cascade: word boundaries → hyphens within a word → characters.
    """
    text = text.strip()
    if not text:
        return []
    if fm.horizontalAdvance(text) <= max_px:
        return [text]

    lines: list[str] = []
    current = ''

    for word in text.split(' '):
        if not word:
            continue
        candidate = (current + ' ' + word) if current else word
        if fm.horizontalAdvance(candidate) <= max_px:
            current = candidate
            continue

        if current:
            lines.append(current)
            current = ''

        if fm.horizontalAdvance(word) <= max_px:
            current = word
            continue

        # Word itself is too wide — try hyphen splits then char splits
        for token in _split_word_at_hyphens(word, fm, max_px):
            t_candidate = (current + token) if current else token
            if fm.horizontalAdvance(t_candidate) <= max_px:
                current = t_candidate
            else:
                if current:
                    lines.append(current)
                    current = ''
                if fm.horizontalAdvance(token) <= max_px:
                    current = token
                else:
                    char_lines = _split_by_chars(token, fm, max_px)
                    lines.extend(char_lines[:-1])
                    current = char_lines[-1] if char_lines else ''

    if current:
        lines.append(current)
    return lines or [text]


def smart_break_label(label: str, fm: QFontMetrics, max_px: int, max_lines: int = _MAX_LABEL_LINES) -> str:
    """Break *label* into lines fitting *max_px*, honouring existing \\n.

    Cascade: spaces → hyphens → characters.  When the result still exceeds
    *max_lines*, flattens and force-wraps char-by-char into at most *max_lines*.
    """
    if not label or max_px <= 0:
        return label or ''

    all_lines: list[str] = []
    for segment in label.split('\n'):
        all_lines.extend(_wrap_one_segment(segment, fm, max_px))

    if len(all_lines) <= max_lines:
        return '\n'.join(all_lines) if all_lines else label

    # Over limit: flatten and force into max_lines via char-level cutting
    flat = ' '.join(label.replace('\n', ' ').split())
    forced: list[str] = []
    current = ''
    for ch in flat:
        if len(forced) >= max_lines - 1:
            test = current + ch
            if fm.horizontalAdvance(test) <= max_px:
                current = test
            # else: silently truncate — text genuinely too long for max_lines
        else:
            test = current + ch
            if fm.horizontalAdvance(test) <= max_px:
                current = test
            else:
                if current:
                    forced.append(current)
                current = ch
    if current and len(forced) < max_lines:
        forced.append(current)
    return '\n'.join(forced) if forced else label[:1]


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
            QSize(min_w, ih + label_gap + fm.lineSpacing() * _MIN_LABEL_LINES + horizontal_pad + _ITEM_INSET_LR + _ITEM_EXTRA_PAD),
            True,
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

    text_zone_w = max(30, cell_w - TILE_H_MARGIN)

    # Pre-break every label with the smart algorithm and count the maximum line count.
    # Since we own all line breaks, cell height is exact — no estimation via boundingRect.
    broken_for_height = [smart_break_label(lbl, fm, text_zone_w) for lbl in (labels or ["—"])]
    max_n_lines = max(lbl.count('\n') + 1 for lbl in broken_for_height)
    max_n_lines = max(max_n_lines, _MIN_LABEL_LINES)
    text_h = max_n_lines * fm.lineSpacing()

    _LOG.debug(
        "cell_size icon=%dpx vp=%d count=%d text_w=%d min_w=%d "
        "computed=%d long_name=%d cap=%s → cell_w=%d zone=%d lines=%d",
        iw, viewport_width, count, text_w, min_w,
        cell_w_before_long, cell_w_before_cap,
        str(max_cell_width),
        cell_w, text_zone_w, max_n_lines,
    )

    cell_h = ih + label_gap + text_h + horizontal_pad + _ITEM_INSET_LR + _ITEM_EXTRA_PAD
    return QSize(cell_w, cell_h), True


def icon_list_spacing_for_cell(icon_size: QSize) -> int:
    """Inter-tile spacing scaled with icon preset."""
    h = max(1, icon_size.height())
    return max(4, min(12, h // 8))


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
    return rows * ch + rows * spacing


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
    # Fixed max tile widths: 3×48=144 for small/medium, icon+96 for large (192px), icon-driven for xlarge.
    if iw >= 256:
        max_cw = iw + 20          # xlarge: 276px → text zone ≈ 250px
    elif iw >= 96:
        max_cw = iw + 96          # large: 192px → text zone ≈ 166px (avoids 3-line wrapping)
    else:
        max_cw = 3 * 48           # small/medium: 144px

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
    list_widget.setTextElideMode(Qt.TextElideMode.ElideNone)
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
