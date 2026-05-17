# File grouped_icon_sections.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Collapsible icon sections (by type / location) for the main window."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable, Iterable

_LOG = logging.getLogger(__name__)

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QFrame,
    QListView,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from utils.device_bundles import DeviceBundle, bundle_location_label
from utils.details_payload import format_device_type_for_details

from .icon_grid_layout import (
    IconListViewportResizeFilter,
    TILE_H_MARGIN,
    apply_icon_mode_list_layout,
    break_label_for_width,
    format_icon_tile_label,
    icon_list_spacing_for_cell,
    icon_mode_label_font,
)
from PySide6.QtGui import QFontMetrics

from .icon_list_qss import ICON_MODE_LIST_QSS
from .no_focus_item_delegate import NoFocusItemDelegate


class IconGroupSection(QFrame):
    """One titled, collapsible block with an icon-mode ``QListWidget``."""

    toggled = Signal(str, bool)

    def __init__(
        self,
        group_id: str,
        title: str,
        bundles: list[DeviceBundle],
        *,
        expanded: bool,
        icon_for_bundle: Callable[[DeviceBundle], object],
        tooltip_for_bundle: Callable[[DeviceBundle], str],
        icon_size: QSize,
        on_tile_context_menu: Callable[[QPoint, DeviceBundle], None] | None = None,
    ) -> None:
        super().__init__()
        self._group_id = group_id
        self.setObjectName("nnIconGroupSection")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "QFrame#nnIconGroupSection { background-color: palette(base); border: none; }"
        )
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._bundles_for_items = list(bundles)
        self._on_tile_context_menu_cb = on_tile_context_menu
        self._icon_size = QSize(icon_size)
        self._item_labels: list[str] = []

        self._header = QToolButton()
        self._header.setObjectName("nnIconGroupHeader")
        self._header.setStyleSheet(
            "QToolButton#nnIconGroupHeader {"
            " background-color: palette(button);"
            " color: palette(button-text);"
            " border: none;"
            " border-radius: 0px;"
            " padding: 6px 8px;"
            " font-weight: bold;"
            "}"
            "QToolButton#nnIconGroupHeader:hover {"
            " background-color: palette(light);"
            "}"
        )
        self._header.setText(title)
        self._header.setCheckable(True)
        self._header.setChecked(expanded)
        self._header.setAutoRaise(False)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self._header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._header.toggled.connect(self._on_header_toggled)

        self._list = QListWidget()
        self._list.setViewMode(QListWidget.ViewMode.IconMode)
        self._list.setMovement(QListWidget.Movement.Static)
        self._list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._list.setSpacing(icon_list_spacing_for_cell(self._icon_size))
        self._list.setIconSize(self._icon_size)
        self._list.setWrapping(True)
        self._list.setWordWrap(True)
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self._list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._list.setLayoutMode(QListView.LayoutMode.Batched)
        self._list.setBatchSize(32)
        self._list.setStyleSheet(ICON_MODE_LIST_QSS)
        self._list.setItemDelegate(NoFocusItemDelegate(self._list, elide_none=True))
        self._viewport_resize_filter = IconListViewportResizeFilter(
            self._relayout_icon_grid, self
        )
        self._list.viewport().installEventFilter(self._viewport_resize_filter)

        if self._on_tile_context_menu_cb is not None:
            self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self._list.customContextMenuRequested.connect(self._on_list_context_menu)

        self._list.setVisible(False)
        vp = self._list.viewport()
        self._list.setUpdatesEnabled(False)
        vp.setUpdatesEnabled(False)
        try:
            for bundle in bundles:
                d = bundle.primary
                raw = (d.name or "").strip() or "-"
                name = format_icon_tile_label(raw)
                self._item_labels.append(name)
                it = QListWidgetItem(icon_for_bundle(bundle), name)
                it.setToolTip(tooltip_for_bundle(bundle))
                self._list.addItem(it)
        finally:
            self._list.setUpdatesEnabled(True)
            vp.setUpdatesEnabled(True)

        self._relayout_icon_grid()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Plain)
        sep.setObjectName("nnIconGroupSeparator")
        outer.addWidget(sep)
        outer.addWidget(self._list)
        # setVisible must be called after addWidget so the list is already parented;
        # calling it on a parentless widget would create a transient top-level OS window.
        self._list.setVisible(expanded)

    def _relayout_icon_grid(self) -> None:
        fallback = 0
        if self._list.isVisible():
            fallback = max(200, self.width() - 16)
            parent = self.parentWidget()
            while parent is not None:
                if isinstance(parent, QScrollArea):
                    fallback = max(fallback, parent.viewport().width() - 16)
                    break
                parent = parent.parentWidget()
        apply_icon_mode_list_layout(
            self._list,
            self._icon_size,
            self._item_labels,
            compact_height=True,
            fallback_viewport_width=fallback,
        )
        cell_w = self._list.gridSize().width()
        if cell_w > 0:
            fm = QFontMetrics(icon_mode_label_font(self._list))
            text_zone = max(30, cell_w - TILE_H_MARGIN)
            for i, orig in enumerate(self._item_labels):
                it = self._list.item(i)
                if it is None:
                    continue
                broken = break_label_for_width(orig, fm, text_zone)
                if _LOG.isEnabledFor(logging.DEBUG) and it.text() != broken:
                    _LOG.debug("  break label %r → %r", orig[:40], broken[:60])
                if it.text() != broken:
                    it.setText(broken)

    def resync_icons(self, icon_for_bundle: Callable[[DeviceBundle], object]) -> None:
        for row, bundle in enumerate(self._bundles_for_items):
            it = self._list.item(row)
            if it is None:
                continue
            it.setIcon(icon_for_bundle(bundle))
        self._relayout_icon_grid()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._relayout_icon_grid()

    def _on_list_context_menu(self, pos: QPoint) -> None:
        if self._on_tile_context_menu_cb is None:
            return
        it = self._list.itemAt(pos)
        if it is None:
            return
        row = self._list.row(it)
        if row < 0 or row >= len(self._bundles_for_items):
            return
        gpos = self._list.viewport().mapToGlobal(pos)
        self._on_tile_context_menu_cb(gpos, self._bundles_for_items[row])

    def _on_header_toggled(self, expanded: bool) -> None:
        self._header.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self._list.setVisible(expanded)
        if expanded:
            self._relayout_icon_grid()
        self.toggled.emit(self._group_id, expanded)


def bucket_bundles_by_type(
    ordered_bundles: Iterable[DeviceBundle],
) -> OrderedDict[str, list[DeviceBundle]]:
    buckets: OrderedDict[str, list[DeviceBundle]] = OrderedDict()
    for b in ordered_bundles:
        slug = (b.primary.type or "unknown").strip().lower()
        buckets.setdefault(slug, []).append(b)
    return buckets


def bucket_bundles_by_location(
    ordered_bundles: Iterable[DeviceBundle],
    *,
    no_location_label: str,
) -> OrderedDict[str, list[DeviceBundle]]:
    buckets: OrderedDict[str, list[DeviceBundle]] = OrderedDict()
    for b in ordered_bundles:
        loc = bundle_location_label(b, no_location_label=no_location_label)
        buckets.setdefault(loc, []).append(b)
    return buckets


def ordered_type_group_ids(buckets: OrderedDict[str, list[DeviceBundle]]) -> list[str]:
    def label_for(slug: str) -> str:
        ref = buckets[slug][0].primary
        return format_device_type_for_details(ref)

    return sorted(buckets.keys(), key=lambda s: label_for(s).lower())


def ordered_location_group_keys(
    buckets: OrderedDict[str, list[DeviceBundle]],
    *,
    no_location_label: str,
) -> list[str]:
    keys = list(buckets.keys())
    rest = [k for k in keys if k != no_location_label]
    rest.sort(key=lambda k: k.lower())
    out = rest
    if no_location_label in buckets:
        out = rest + [no_location_label]
    return out


def build_grouped_icon_scroll(
    parent: QWidget | None,
    *,
    mode: str,
    ordered_bundles: list[DeviceBundle],
    no_location_label: str,
    expanded_map: dict[str, bool],
    icon_for_bundle: Callable[[DeviceBundle], object],
    tooltip_for_bundle: Callable[[DeviceBundle], str],
    icon_size: QSize,
    on_section_toggled: Callable[[str, bool], None],
    on_tile_context_menu: Callable[[QPoint, DeviceBundle], None] | None = None,
) -> QScrollArea:
    """Build scroll area with one collapsible section per group; ``mode`` is ``sorted`` or ``location``."""
    scroll = QScrollArea(parent)
    scroll.setObjectName("nnGroupedIconScroll")
    scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    scroll.setStyleSheet(
        "QScrollArea#nnGroupedIconScroll { background-color: palette(base); border: none; }"
    )
    scroll.setVisible(False)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    vp = scroll.viewport()
    vp.setObjectName("nnGroupedIconViewport")
    vp.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    vp.setStyleSheet("#nnGroupedIconViewport { background-color: palette(base); }")

    inner = QWidget()
    inner.setObjectName("nnGroupedIconInner")
    inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    inner.setStyleSheet("#nnGroupedIconInner { background-color: palette(base); }")
    layout = QVBoxLayout(inner)
    layout.setContentsMargins(8, 0, 8, 12)
    layout.setSpacing(10)

    if mode == "sorted":
        buckets = bucket_bundles_by_type(ordered_bundles)
        order = ordered_type_group_ids(buckets)
    elif mode == "location":
        buckets = bucket_bundles_by_location(ordered_bundles, no_location_label=no_location_label)
        order = ordered_location_group_keys(buckets, no_location_label=no_location_label)
    else:
        buckets = OrderedDict()
        order = []

    sections: list[IconGroupSection] = []

    for slug_or_loc in order:
        group_bundles = buckets.get(slug_or_loc, [])
        if not group_bundles:
            continue
        if mode == "sorted":
            group_id = f"type:{slug_or_loc}"
            title_text = "{} ({})".format(
                format_device_type_for_details(group_bundles[0].primary),
                len(group_bundles),
            )
        else:
            group_id = (
                "location:__none__"
                if slug_or_loc == no_location_label
                else f"location:{slug_or_loc}"
            )
            title_text = "{} ({})".format(slug_or_loc, len(group_bundles))

        expanded = expanded_map.get(group_id, True)
        section = IconGroupSection(
            group_id,
            title_text,
            group_bundles,
            expanded=expanded,
            icon_for_bundle=icon_for_bundle,
            tooltip_for_bundle=tooltip_for_bundle,
            icon_size=icon_size,
            on_tile_context_menu=on_tile_context_menu,
        )
        section.toggled.connect(on_section_toggled)
        layout.addWidget(section)
        sections.append(section)

    def _relayout_all_sections() -> None:
        for section in sections:
            section._relayout_icon_grid()

    scroll._icon_group_sections = sections  # type: ignore[attr-defined]
    scroll._icon_sections_relayout_filter = IconListViewportResizeFilter(
        _relayout_all_sections, scroll
    )
    vp.installEventFilter(scroll._icon_sections_relayout_filter)

    layout.addStretch(1)
    scroll.setWidget(inner)
    scroll.setVisible(True)
    _relayout_all_sections()
    return scroll
