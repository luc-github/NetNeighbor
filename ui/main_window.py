# File main_window.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Main window — list & icon views (NetNeighbor 2.0)."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from datetime import datetime, timezone

from gettext import gettext as _
from PySide6.QtCore import QDateTime, QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence, QPainter, QPalette, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from discovery.manager import DiscoveryManager
from model.device import Device
from ui.icons import resolve_asset_icon_file, resolve_persisted_icon_id_to_path
from ui.grouped_icon_sections import build_grouped_icon_scroll
from ui.icon_grid_layout import (
    IconListViewportResizeFilter,
    create_icon_tile_list_item,
    relayout_icon_mode_list,
    set_icon_tile_item_label,
)
from ui.device_actions import (
    bundle_custom_command,
    launch_open_uri,
    prompt_rename_device,
    run_custom_command_for_bundle,
)
from ui.device_context_menu import show_device_context_menu
from ui.device_details_dialog import DeviceCommandSettings, DeviceIconSettings, show_device_details_dialog
from ui.icon_picker_dialog import pick_device_icon_id
from ui.icon_tile_delegate import IconTileItemDelegate
from ui.no_focus_item_delegate import NoFocusItemDelegate
from utils.app_version import get_app_version
from utils.details_payload import format_device_type_for_details
from utils.device_bundles import (
    DeviceBundle,
    apply_bundle_category_filter,
    build_device_bundles,
    bundle_location_label,
    bundle_snapshot_ui_fingerprint,
)
from utils.connect_launcher import normalize_connect_templates
from utils.device_details_view import build_device_details_view_model
from utils.device_remote_icon import (
    bundle_has_device_icon_source,
    bundle_provided_icon_display,
)
from utils.discovery_config import normalize_information_precedence_list
from utils.double_click_open import resolve_all_connect_targets, resolve_connect_target
from utils.location_label import normalize_location_options
from utils.icon_view_prefs import (
    DEVICE_ICON_REFERENCE_PX,
    ICON_SIZE_PRESET_PIXELS,
    icon_size_preset_to_qsize,
    normalize_icon_size_preset,
)
from ui.remote_icon_cache import QtRemoteIconCache
from utils.qt_device_icons import qt_icon_for_device_type
from utils.ui_prefs import load_ui_preferences, save_ui_preferences

from .icon_list_qss import ICON_MODE_LIST_QSS


_LOG = logging.getLogger("ui")


_LIST_ITEM_INTERACTION_QSS = (
    "QListWidget { background-color: palette(base); outline: none; show-decoration-selected: 0; }"
    "QListWidget::item { padding: 4px 6px; border: 1px solid transparent; }"
    "QListWidget::item:hover {"
    " background-color: palette(alternate-base);"
    "}"
    "QListWidget::item:selected, QListWidget::item:selected:active, QListWidget::item:selected:!active {"
    " background-color: palette(highlight);"
    " color: palette(highlighted-text);"
    " border: none;"
    " outline: none;"
    "}"
    "QListWidget::item:selected:hover {"
    " background-color: palette(highlight);"
    " color: palette(highlighted-text);"
    "}"
    "QListWidget::item:focus { border: none; outline: none; }"
)

_TABLE_BODY_QSS = (
    "QTableWidget {"
    " gridline-color: palette(mid);"
    " background-color: palette(base);"
    " alternate-background-color: palette(alternate-base);"
    " outline: none;"
    "}"
    "QTableWidget::item { padding: 4px 6px; border: 1px solid transparent; }"
    "QTableWidget::item:selected, QTableWidget::item:selected:active, QTableWidget::item:selected:!active {"
    " background-color: palette(highlight);"
    " color: palette(highlighted-text);"
    " border: none;"
    " outline: none;"
    "}"
    "QTableWidget::item:focus { border: none; outline: none; }"
)


class _ScanningOverlay(QWidget):
    """Animated spinner overlay — a background thread drives the animation via Signal.

    Using a Python thread + Signal (QueuedConnection) ensures the repaint request
    reaches the main thread even when it is briefly busy at startup, without any
    dependency on QTimer delivery timing.
    """

    _FRAMES = 10
    _INTERVAL_S = 0.10   # 10 fps
    _MAX_MS = 12_000
    # SVG viewBox is 200×250 (4:5 ratio); render at this width (height computed from ratio).
    _SVG_W = 160
    _SVG_H = 200  # 160 * 250 / 200

    _tick_signal = Signal()  # emitted from bg thread → received on main thread

    def __init__(self, main_window: QWidget, content_frame: QWidget) -> None:
        super().__init__(main_window)
        self._content_frame = content_frame
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._step = 0
        self._tick_count = 0
        self._hide_requested = False
        self._alive = False
        self._tick_signal.connect(self._on_tick, Qt.ConnectionType.QueuedConnection)
        self._guard = QTimer(self)
        self._guard.setSingleShot(True)
        self._guard.setInterval(self._MAX_MS)
        self._guard.timeout.connect(self.hide)
        content_frame.installEventFilter(self)
        main_window.installEventFilter(self)

        from PySide6.QtSvg import QSvgRenderer
        svg_dir = Path(__file__).resolve().parent.parent / "assets" / "spinner"
        self._renderers: list = []
        for i in range(self._FRAMES):
            p = svg_dir / f"spinner-{i}.svg"
            r = QSvgRenderer(str(p), self) if p.is_file() else None
            self._renderers.append(r)

    # ── geometry sync ──────────────────────────────────────────────────────────

    def _update_geometry(self) -> None:
        p = self._content_frame
        mw = self.parent()
        if p is None or mw is None:
            return
        tl = p.mapTo(mw, QPoint(0, 0))
        self.setGeometry(tl.x(), tl.y(), p.width(), p.height())

    def eventFilter(self, obj, event) -> bool:
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self._update_geometry()
            if self.isVisible():
                self.raise_()
        return False

    # ── show / hide ────────────────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_geometry()
        self.raise_()
        self._guard.start()
        self._tick_count = 0
        self._hide_requested = False
        self._alive = True
        t = threading.Thread(target=self._anim_loop, daemon=True, name="overlay-anim")
        t.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._alive = False
        self._guard.stop()

    # ── animation thread ───────────────────────────────────────────────────────

    def _anim_loop(self) -> None:
        while self._alive:
            self._tick_signal.emit()
            time.sleep(self._INTERVAL_S)

    def request_hide(self) -> None:
        """Hide after the current cycle completes (minimum one full 0→9 loop)."""
        if self._tick_count >= self._FRAMES:
            self.hide()
        else:
            self._hide_requested = True

    def _on_tick(self) -> None:
        if not self.isVisible():
            return
        self._tick_count += 1
        self._step = self._tick_count % self._FRAMES
        self.raise_()
        self.repaint()
        if self._hide_requested and self._tick_count >= self._FRAMES:
            self.hide()

    # ── painting ───────────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        from PySide6.QtCore import QRectF
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.palette().color(QPalette.ColorRole.Base))

        cx = self.width() / 2.0
        # Centre the SVG slightly above mid-height to leave room for text below.
        svg_top = self.height() / 2.0 - self._SVG_H / 2.0 - 16
        svg_rect = QRectF(cx - self._SVG_W / 2.0, svg_top, self._SVG_W, self._SVG_H)

        renderer = self._renderers[self._step % self._FRAMES] if self._renderers else None
        if renderer is not None and renderer.isValid():
            renderer.render(painter, svg_rect)

        font = painter.font()
        font.setPointSize(11)
        painter.setFont(font)
        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        text_top = int(svg_top + self._SVG_H + 10)
        painter.drawText(
            QRect(0, text_top, self.width(), 32),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            _("Start Scanning…"),
        )
        painter.end()


class _UserActivityGuard(QObject):
    """Application-level event filter that tracks the last user input timestamp."""

    _WATCHED = frozenset({
        QEvent.Type.MouseMove,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.Wheel,
        QEvent.Type.KeyPress,
    })

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._last_ms: int = 0

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() in self._WATCHED:
            self._last_ms = QDateTime.currentMSecsSinceEpoch()
        return False

    def idle_ms(self) -> int:
        """Milliseconds since the last user input event."""
        return QDateTime.currentMSecsSinceEpoch() - self._last_ms


class NetNeighborMainWindow(QMainWindow):
    """Table (list) and icon-mode list; view options stored in ``ui_prefs.json``."""

    _notification_received = Signal(str, str, str)  # datetime_str, device_name, status

    _FILTER_ROLE = Qt.ItemDataRole.UserRole
    _BUNDLE_KEY_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(
        self,
        parent=None,
        *,
        discovery_manager: DiscoveryManager | None = None,
        information_precedence: Sequence[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._discovery_manager = discovery_manager
        self._information_precedence = (
            list(information_precedence)
            if information_precedence
            else normalize_information_precedence_list(None)
        )

        prefs = load_ui_preferences()
        self._view_mode = str(prefs.get("view_mode", "icons"))
        if self._view_mode not in {"icons", "list"}:
            self._view_mode = "icons"
        self._icon_sort_mode = str(prefs.get("icon_sort_mode", "sorted"))
        if self._icon_sort_mode not in {"appearance", "sorted", "location"}:
            self._icon_sort_mode = "sorted"

        self._icon_size_preset = normalize_icon_size_preset(prefs.get("icon_size_preset"))

        sc = prefs.get("selected_category")
        self._selected_category: str | None = sc if isinstance(sc, str) else None

        self._sidebar_position = int(prefs.get("sidebar_position", 220))
        self._sidebar_position = max(160, min(self._sidebar_position, 480))
        self._sidebar_collapsed_pref = bool(prefs.get("sidebar_collapsed", False))

        raw_exp = prefs.get("icon_group_expanded")
        self._icon_group_expanded: dict[str, bool] = {}
        if isinstance(raw_exp, dict):
            for k, v in raw_exp.items():
                if isinstance(k, str) and isinstance(v, bool):
                    self._icon_group_expanded[k] = v

        self._icon_source_overrides: dict[tuple[str, int], str] = {}
        raw_iso = prefs.get("icon_source_overrides")
        if isinstance(raw_iso, dict):
            for key, mode in raw_iso.items():
                if mode not in {"auto", "provided", "system", "custom"}:
                    continue
                if not isinstance(key, str) or ":" not in key:
                    continue
                ip, port_text = key.rsplit(":", 1)
                try:
                    port = int(port_text)
                except ValueError:
                    continue
                self._icon_source_overrides[(ip, port)] = mode

        self._custom_icon_overrides: dict[tuple[str, int], str] = {}
        raw_cio = prefs.get("custom_icon_overrides")
        if isinstance(raw_cio, dict):
            for key, icon_name in raw_cio.items():
                if not isinstance(icon_name, str) or not icon_name.strip():
                    continue
                if not isinstance(key, str) or ":" not in key:
                    continue
                ip, port_text = key.rsplit(":", 1)
                try:
                    port = int(port_text)
                except ValueError:
                    continue
                self._custom_icon_overrides[(ip, port)] = icon_name.strip()

        self._location_options: list[str] = []
        raw_locs = prefs.get("location_options")
        if isinstance(raw_locs, list):
            self._location_options = normalize_location_options(
                [str(v).strip() for v in raw_locs if isinstance(v, str) and str(v).strip()]
            )
        self._type_options: list[tuple[str, str]] = []
        raw_types = prefs.get("type_options")
        if isinstance(raw_types, list):
            for entry in raw_types:
                if not isinstance(entry, dict):
                    continue
                label = entry.get("label")
                slug = entry.get("slug")
                if isinstance(label, str) and isinstance(slug, str) and slug.strip():
                    self._type_options.append((label, slug.strip().lower()))
        self._connect_command_templates = normalize_connect_templates(
            prefs.get("connect_command_templates")
        )
        self._custom_command_template = str(prefs.get("custom_command_template", "") or "")

        if discovery_manager is not None:
            raw_dc = prefs.get("device_commands")
            if isinstance(raw_dc, dict):
                discovery_manager.set_device_commands_overrides(raw_dc)
            raw_cc = prefs.get("custom_command_overrides")
            if isinstance(raw_cc, dict):
                discovery_manager.set_custom_command_overrides(raw_cc)
            raw_fmr = prefs.get("field_mapping_rules")
            if isinstance(raw_fmr, dict):
                discovery_manager.set_field_mapping_rules(raw_fmr)
            raw_mon = prefs.get("monitored_overrides")
            if isinstance(raw_mon, dict):
                discovery_manager.set_monitored_overrides(raw_mon)
            raw_hidden = prefs.get("hidden_overrides")
            if isinstance(raw_hidden, dict):
                discovery_manager.set_hidden_overrides(raw_hidden)

        raw_hidden_meta = prefs.get("hidden_device_meta")
        self._hidden_device_meta: dict[str, dict[str, object]] = {}
        if isinstance(raw_hidden_meta, dict):
            for key, value in raw_hidden_meta.items():
                if isinstance(key, str) and isinstance(value, dict):
                    self._hidden_device_meta[key] = dict(value)

        self._notification_log: list[tuple[str, str, str]] = []
        self._notification_received.connect(self._append_notification)
        if discovery_manager is not None:
            discovery_manager.register_presence_transition_hook(
                self._on_presence_transition
            )

        self._last_devices: list[Device] = []
        self._bundles: list[DeviceBundle] = []
        self._sidebar_signature: tuple | None = None
        self._last_device_view_sig: object | None = None
        self._last_snapshot_fp: object | None = None
        self._first_device_ui_flush_done = False
        self._is_updating_sidebar = False
        self._table_sort_programmatic = False

        self._pending_devices: list[Device] | None = None
        self._device_refresh_timer = QTimer(self)
        self._device_refresh_timer.setSingleShot(True)
        self._device_refresh_timer.timeout.connect(self._flush_pending_devices)

        self._view_refresh_timer = QTimer(self)
        self._view_refresh_timer.setSingleShot(True)
        self._view_refresh_timer.timeout.connect(self._refresh_device_widgets)

        self._persist_prefs_timer = QTimer(self)
        self._persist_prefs_timer.setSingleShot(True)
        self._persist_prefs_timer.timeout.connect(self._persist_ui_prefs)

        self._activity_guard = _UserActivityGuard(self)
        _app = QApplication.instance()
        if _app is not None:
            _app.installEventFilter(self._activity_guard)

        self._remote_icon_cache = QtRemoteIconCache(self)
        self._remote_icon_cache.icons_ready.connect(self._on_remote_icons_ready)
        self._remote_icon_cache.prefetch_from_index()

        ver = get_app_version()
        self.setWindowTitle(_("NetNeighbor {}").format(ver))

        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        self._sidebar_list = QListWidget()
        self._sidebar_list.setMinimumWidth(140)
        self._sidebar_list.setWordWrap(True)
        self._sidebar_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._sidebar_list.itemSelectionChanged.connect(self._on_sidebar_selection_changed)
        self._sidebar_list.setStyleSheet(_LIST_ITEM_INTERACTION_QSS)
        self._sidebar_list.setItemDelegate(NoFocusItemDelegate(self._sidebar_list))

        self._sidebar_top_spacer = QWidget()
        self._sidebar_top_spacer.setFixedHeight(0)
        self._sidebar_top_spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._sidebar_host = QWidget()
        _sidebar_col = QVBoxLayout(self._sidebar_host)
        _sidebar_col.setContentsMargins(0, 0, 0, 0)
        _sidebar_col.setSpacing(0)
        _sidebar_col.addWidget(self._sidebar_top_spacer)
        _sidebar_col.addWidget(self._sidebar_list, stretch=1)
        self._sidebar_host.setMinimumWidth(140)
        self._sidebar_host.setObjectName("nnSidebarFrame")
        self._sidebar_host.setStyleSheet(
            "#nnSidebarFrame {"
            " border: 1px solid palette(mid);"
            " border-radius: 2px;"
            " background: palette(base);"
            "}"
        )

        self._stack = QStackedWidget()
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            [
                _("Name"),
                _("IP"),
                _("Type"),
                _("Location"),
                _("Online"),
            ]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        self._table.cellDoubleClicked.connect(self._on_table_double_clicked)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setStyleSheet(
            "QHeaderView::section {"
            " background-color: palette(button);"
            " color: palette(button-text);"
            " padding: 4px 6px;"
            " border: none;"
            " border-right: 1px solid palette(mid);"
            " border-bottom: 1px solid palette(mid);"
            "}"
        )
        self._table.setStyleSheet(_TABLE_BODY_QSS)
        self._table.setItemDelegate(NoFocusItemDelegate(self._table))
        self._table.horizontalHeader().sortIndicatorChanged.connect(self._on_table_sort_indicator_changed)

        self._icon_list = QListWidget()
        self._icon_list.setViewMode(QListWidget.ViewMode.IconMode)
        self._icon_list.setUniformItemSizes(True)
        self._icon_list.setMovement(QListWidget.Movement.Static)
        self._icon_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._icon_list.setWrapping(True)
        self._icon_list.setWordWrap(True)
        self._icon_list.setFrameShape(QFrame.Shape.NoFrame)
        self._icon_list.setViewportMargins(8, 0, 8, 8)
        # Windows native (Vista) style for QListView IconMode can create transient top-level HWNDs
        # per layout pass; batched layout + a minimal stylesheet steer this widget through the
        # style-polished path instead (taskbar entries titled like "python…" still pick up our AppID).
        self._icon_list.setLayoutMode(QListView.LayoutMode.Batched)
        self._icon_list.setBatchSize(32)
        self._icon_list.setStyleSheet(ICON_MODE_LIST_QSS)
        self._icon_list.setItemDelegate(IconTileItemDelegate(self._icon_list))
        self._icon_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._icon_list.customContextMenuRequested.connect(self._on_icon_list_context_menu)
        self._icon_list.itemDoubleClicked.connect(self._on_icon_list_double_clicked)
        self._flat_icon_viewport_filter = IconListViewportResizeFilter(
            self._relayout_flat_icon_list, self
        )
        self._icon_list.viewport().installEventFilter(self._flat_icon_viewport_filter)
        self._apply_icon_list_dimensions()

        self._icon_page_stack = QStackedWidget()
        self._icon_page_stack.addWidget(self._icon_list)

        self._grouped_icons_page = QWidget()
        self._grouped_icons_page.setObjectName("nnGroupedIconsPage")
        self._grouped_icons_page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._grouped_icons_page.setStyleSheet(
            "#nnGroupedIconsPage { background-color: palette(base); }"
        )
        self._grouped_icons_layout = QVBoxLayout(self._grouped_icons_page)
        self._grouped_icons_layout.setContentsMargins(0, 0, 0, 0)
        self._grouped_icons_layout.setSpacing(0)
        self._icon_page_stack.addWidget(self._grouped_icons_page)

        self._stack.addWidget(self._table)
        self._stack.addWidget(self._icon_page_stack)

        self._sidebar_toggle_btn = QToolButton(self)
        self._sidebar_toggle_btn.setObjectName("nnSidebarToggle")
        self._sidebar_toggle_btn.setAutoRaise(True)
        self._sidebar_toggle_btn.setFixedWidth(14)
        self._sidebar_toggle_btn.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
        )
        self._sidebar_toggle_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._sidebar_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sidebar_toggle_btn.clicked.connect(self._on_sidebar_handle_clicked)
        self._sidebar_toggle_btn.setStyleSheet(
            "QToolButton#nnSidebarToggle {"
            " background-color: palette(button);"
            " border: none;"
            " border-left: 1px solid palette(mid);"
            " border-right: 1px solid palette(mid);"
            " color: palette(mid);"
            " font-size: 11px;"
            " padding: 0px;"
            "}"
            "QToolButton#nnSidebarToggle:hover {"
            " background-color: palette(alternate-base);"
            " border-left-color: palette(alternate-base);"
            " border-right-color: palette(alternate-base);"
            " color: palette(text);"
            "}"
        )

        self._content_frame = QFrame()
        self._content_frame.setObjectName("nnContentFrame")
        self._content_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._content_frame.setFrameShadow(QFrame.Shadow.Plain)
        self._content_frame.setLineWidth(1)
        self._content_frame.setStyleSheet(
            "#nnContentFrame {"
            " border: 1px solid palette(mid);"
            " border-radius: 2px;"
            " background: palette(base);"
            "}"
        )
        _content_layout = QVBoxLayout(self._content_frame)
        _content_layout.setContentsMargins(0, 0, 0, 0)
        _content_layout.setSpacing(0)
        _content_layout.addWidget(self._stack)

        self._sidebar_toggle_host = QWidget()
        _main_row = QHBoxLayout(self._sidebar_toggle_host)
        _main_row.setContentsMargins(0, 0, 0, 0)
        _main_row.setSpacing(0)
        _main_row.addWidget(self._sidebar_toggle_btn)
        _main_row.addWidget(self._content_frame, stretch=1)

        self._splitter.addWidget(self._sidebar_host)
        self._splitter.addWidget(self._sidebar_toggle_host)
        self._splitter.setChildrenCollapsible(True)
        self._splitter.setCollapsible(0, True)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)

        total_w = max(self.width(), 900)
        aside = min(self._sidebar_position, total_w // 2)
        self._splitter.setSizes([aside, total_w - aside])
        self._refresh_sidebar_toggle_appearance()

        self.setCentralWidget(self._splitter)

        self.statusBar().showMessage(_("No devices yet"))

        self._setup_menu_bar()
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        self._sync_menu_checks_from_state()
        self._apply_stack_page()
        self._rebuild_sidebar()
        QTimer.singleShot(0, self._apply_initial_sidebar_geometry)

        self._scanning_overlay = _ScanningOverlay(self, self._content_frame)
        self._scanning_overlay.show()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_sidebar_top_spacer)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._sync_sidebar_top_spacer()
        if self._view_mode == "icons" and self._icon_sort_mode == "appearance":
            self._relayout_flat_icon_list()

    def _no_location_label(self) -> str:
        return _("No location")

    def _sidebar_group_mode(self) -> str:
        if self._icon_sort_mode == "location":
            return "location"
        return "category"

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        self._sync_sidebar_menu_from_splitter()
        self._persist_ui_prefs()

    def _sync_sidebar_menu_from_splitter(self) -> None:
        sz = self._splitter.sizes()
        visible = bool(sz and sz[0] > 8)
        self._act_sidebar.blockSignals(True)
        self._act_sidebar.setChecked(visible)
        self._act_sidebar.blockSignals(False)
        self._refresh_sidebar_toggle_appearance()

    def _refresh_sidebar_toggle_appearance(self) -> None:
        sz = self._splitter.sizes()
        visible = bool(sz and sz[0] > 8)
        if visible:
            self._sidebar_toggle_btn.setText("◀")
            self._sidebar_toggle_btn.setToolTip(_("Hide sidebar"))
        else:
            self._sidebar_toggle_btn.setText("▶")
            self._sidebar_toggle_btn.setToolTip(_("Show sidebar"))

    def _on_sidebar_handle_clicked(self) -> None:
        sz = self._splitter.sizes()
        visible = bool(sz and sz[0] > 8)
        self._on_sidebar_toggled(not visible)
        self._sync_sidebar_menu_from_splitter()

    def _apply_initial_sidebar_geometry(self) -> None:
        total = max(self._splitter.width(), self.width(), 600)
        if self._sidebar_collapsed_pref:
            self._splitter.setSizes([0, total])
            self._act_sidebar.blockSignals(True)
            self._act_sidebar.setChecked(False)
            self._act_sidebar.blockSignals(False)
            self._refresh_sidebar_toggle_appearance()
        else:
            aside = min(self._sidebar_position, total // 2)
            self._splitter.setSizes([aside, total - aside])
            self._sync_sidebar_menu_from_splitter()
        self._sync_sidebar_top_spacer()

    def _on_sidebar_toggled(self, visible: bool) -> None:
        total = max(self._splitter.width(), self.width(), 600)
        if visible:
            aside = max(160, min(self._sidebar_position, total // 2))
            self._splitter.setSizes([aside, total - aside])
        else:
            sz = self._splitter.sizes()
            if sz and sz[0] > 8:
                self._sidebar_position = max(160, min(int(sz[0]), 480))
            self._splitter.setSizes([0, total])
        self._persist_ui_prefs()
        self._refresh_sidebar_toggle_appearance()

    def _on_sidebar_selection_changed(self) -> None:
        if self._is_updating_sidebar:
            return
        item = self._sidebar_list.currentItem()
        if item is None:
            return
        raw = item.data(self._FILTER_ROLE)
        if raw is None or raw == "":
            self._selected_category = None
        elif isinstance(raw, str):
            self._selected_category = raw
        else:
            self._selected_category = None
        self._persist_ui_prefs()
        self._schedule_view_refresh()

    def _schedule_view_refresh(self, delay_ms: int = 40) -> None:
        """Coalesce table/icon rebuilds (sidebar filter + discovery) to cut native-widget flicker."""
        self._view_refresh_timer.stop()
        self._view_refresh_timer.start(int(delay_ms))

    def _schedule_persist_ui_prefs(self) -> None:
        """Write prefs on the next event-loop tick (keeps hide/unhide snappy)."""
        self._persist_prefs_timer.start(0)

    def _update_visible_device_count_status(self) -> None:
        n = len(self._filtered_bundles())
        self.statusBar().showMessage(_("{} devices").format(n))

    def _rebuild_sidebar(self) -> None:
        sidebar_mode = self._sidebar_group_mode()
        if self._discovery_manager is not None:
            all_bundles = [b for b in self._bundles if not self._discovery_manager.bundle_is_hidden(b.ip, b.port)]
        else:
            all_bundles = list(self._bundles)
        counts: dict[str, int] = {}
        bundle_filter_keys: dict[str, str] = {}

        for bundle in all_bundles:
            if sidebar_mode == "location":
                location = bundle_location_label(bundle, no_location_label=self._no_location_label())
                counts[location] = counts.get(location, 0) + 1
                nl = self._no_location_label()
                bundle_filter_keys[location] = "location:__none__" if location == nl else f"location:{location}"
            else:
                slug = (bundle.primary.type or "unknown").strip().lower()
                counts[slug] = counts.get(slug, 0) + 1
                bundle_filter_keys[slug] = slug

        signature = (sidebar_mode, len(all_bundles), tuple(sorted(counts.items())))
        if signature == self._sidebar_signature:
            return
        self._sidebar_signature = signature

        type_slug_labels: dict[str, str] = {}
        if sidebar_mode != "location":
            for b in all_bundles:
                slug = (b.primary.type or "unknown").strip().lower()
                if slug not in type_slug_labels:
                    type_slug_labels[slug] = format_device_type_for_details(b.primary)

        valid_filters: set[str | None] = {None}
        valid_filters.update(bundle_filter_keys.values())
        if self._selected_category is not None and self._selected_category not in valid_filters:
            self._selected_category = None

        self._is_updating_sidebar = True
        self._sidebar_list.blockSignals(True)
        self._sidebar_list.clear()

        all_text = _("All Locations") if sidebar_mode == "location" else _("All Types")
        all_label = f"{all_text} ({len(all_bundles)})"
        first = QListWidgetItem(all_label)
        first.setData(self._FILTER_ROLE, None)
        self._sidebar_list.addItem(first)

        for key in sorted(
            counts.keys(),
            key=lambda k: str(k).lower()
            if sidebar_mode == "location"
            else type_slug_labels.get(k, k).lower(),
        ):
            if sidebar_mode == "location" and key == self._no_location_label():
                continue
            label = key if sidebar_mode == "location" else type_slug_labels.get(key, key)
            row = QListWidgetItem(f"{label} ({counts[key]})")
            row.setData(self._FILTER_ROLE, bundle_filter_keys[key])
            self._sidebar_list.addItem(row)

        if sidebar_mode == "location" and self._no_location_label() in counts:
            nl = self._no_location_label()
            row = QListWidgetItem(f"{nl} ({counts[nl]})")
            row.setData(self._FILTER_ROLE, bundle_filter_keys[nl])
            self._sidebar_list.addItem(row)

        target_idx = 0
        for i in range(self._sidebar_list.count()):
            it = self._sidebar_list.item(i)
            fd = it.data(self._FILTER_ROLE)
            if fd == self._selected_category or (
                self._selected_category is None and (fd is None or fd == "")
            ):
                target_idx = i
                break

        self._sidebar_list.setCurrentRow(target_idx)
        self._sidebar_list.blockSignals(False)
        self._is_updating_sidebar = False

    def _setup_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        view_menu = menu_bar.addMenu(_("View"))
        act_reload = QAction(_("Reload discovery"), self)
        act_reload.setShortcut(QKeySequence.StandardKey.Refresh)
        act_reload.triggered.connect(self._reload_discovery)
        act_reload.setEnabled(self._discovery_manager is not None)
        view_menu.addAction(act_reload)

        view_menu.addSeparator()

        self._act_sidebar = QAction(_("Sidebar"), self)
        self._act_sidebar.setCheckable(True)
        self._act_sidebar.setChecked(not self._sidebar_collapsed_pref)
        self._act_sidebar.toggled.connect(self._on_sidebar_toggled)
        view_menu.addAction(self._act_sidebar)

        view_menu.addSeparator()

        view_menu.addAction(_disabled_title_action(self, _("Display:")))
        self._grp_display = QActionGroup(self)
        self._grp_display.setExclusive(True)
        self._act_display_icons = QAction(_("Icons"), self)
        self._act_display_icons.setCheckable(True)
        self._act_display_list = QAction(_("List"), self)
        self._act_display_list.setCheckable(True)
        self._grp_display.addAction(self._act_display_icons)
        self._grp_display.addAction(self._act_display_list)
        view_menu.addAction(self._act_display_icons)
        view_menu.addAction(self._act_display_list)
        self._grp_display.triggered.connect(self._on_display_group_triggered)

        view_menu.addSeparator()

        view_menu.addAction(_disabled_title_action(self, _("Arrange:")))
        self._grp_arrange = QActionGroup(self)
        self._grp_arrange.setExclusive(True)
        self._act_arr_unsorted = QAction(_("Unsorted"), self)
        self._act_arr_unsorted.setCheckable(True)
        self._act_arr_type = QAction(_("by Type"), self)
        self._act_arr_type.setCheckable(True)
        self._act_arr_location = QAction(_("by Location"), self)
        self._act_arr_location.setCheckable(True)
        self._grp_arrange.addAction(self._act_arr_unsorted)
        self._grp_arrange.addAction(self._act_arr_type)
        self._grp_arrange.addAction(self._act_arr_location)
        view_menu.addAction(self._act_arr_unsorted)
        view_menu.addAction(self._act_arr_type)
        view_menu.addAction(self._act_arr_location)
        self._grp_arrange.triggered.connect(self._on_arrange_group_triggered)

        view_menu.addSeparator()

        self._menu_icon_size = QMenu(_("Icons size"), self)
        self._grp_icon_size = QActionGroup(self)
        self._grp_icon_size.setExclusive(True)
        self._act_icon_sz_small = QAction(_("Small"), self)
        self._act_icon_sz_medium = QAction(_("Medium"), self)
        self._act_icon_sz_large = QAction(_("Large"), self)
        self._act_icon_sz_xlarge = QAction(_("Extra large"), self)
        for a in (
            self._act_icon_sz_small,
            self._act_icon_sz_medium,
            self._act_icon_sz_large,
            self._act_icon_sz_xlarge,
        ):
            a.setCheckable(True)
            self._grp_icon_size.addAction(a)
            self._menu_icon_size.addAction(a)
        self._grp_icon_size.triggered.connect(self._on_icon_size_group_triggered)
        self._act_view_menu_icon_size = view_menu.addMenu(self._menu_icon_size)

        view_menu.addSeparator()

        act_prefs = QAction(_("Preferences…"), self)
        act_prefs.setShortcut(QKeySequence.StandardKey.Preferences)
        act_prefs.setMenuRole(QAction.MenuRole.NoRole)
        act_prefs.triggered.connect(self._open_preferences)
        view_menu.addAction(act_prefs)

        view_menu.addSeparator()

        act_quit = QAction(_("Quit"), self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.setMenuRole(QAction.MenuRole.NoRole)
        act_quit.triggered.connect(self._quit_application)
        view_menu.addAction(act_quit)

        tools_menu = menu_bar.addMenu(_("Tools"))
        act_notif = QAction(_("Notifications history"), self)
        act_notif.triggered.connect(self._open_notifications_history)
        tools_menu.addAction(act_notif)
        act_hidden = QAction(_("Hidden devices"), self)
        act_hidden.triggered.connect(self._open_hidden_devices)
        tools_menu.addAction(act_hidden)

        help_menu = menu_bar.addMenu(_("Help"))
        act_about = QAction(_("About NetNeighbor"), self)
        act_about.setMenuRole(QAction.MenuRole.AboutRole)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _on_display_group_triggered(self, action: QAction) -> None:
        if action == self._act_display_icons:
            self._apply_view_mode("icons")
        elif action == self._act_display_list:
            self._apply_view_mode("list")

    def _on_arrange_group_triggered(self, action: QAction) -> None:
        if action == self._act_arr_unsorted:
            mode = "appearance"
        elif action == self._act_arr_type:
            mode = "sorted"
        elif action == self._act_arr_location:
            mode = "location"
        else:
            return
        self._apply_arrange_mode(mode)

    def _on_icon_size_group_triggered(self, action: QAction) -> None:
        if action == self._act_icon_sz_small:
            self._apply_icon_size_preset("small")
        elif action == self._act_icon_sz_medium:
            self._apply_icon_size_preset("medium")
        elif action == self._act_icon_sz_large:
            self._apply_icon_size_preset("large")
        elif action == self._act_icon_sz_xlarge:
            self._apply_icon_size_preset("xlarge")

    def _apply_icon_size_preset(self, preset: str) -> None:
        if preset not in ICON_SIZE_PRESET_PIXELS:
            return
        self._icon_size_preset = preset
        self._persist_ui_prefs()
        self._apply_icon_list_dimensions()
        if self._view_mode != "icons":
            return
        self.setUpdatesEnabled(False)
        try:
            if self._icon_sort_mode == "appearance":
                self._resync_flat_icon_list_pixmaps()
            else:
                self._rebuild_grouped_icon_page(self._current_ordered_bundles_for_view())
        finally:
            self.setUpdatesEnabled(True)
        ordered = self._current_ordered_bundles_for_view()
        self._last_device_view_sig = self._device_view_refresh_signature(ordered)

    def _apply_icon_list_dimensions(self) -> None:
        self._relayout_flat_icon_list(force=True)

    def _relayout_flat_icon_list(self, *, force: bool = False) -> None:
        if self._icon_list.count() <= 0:
            return
        fallback = 0
        if self._icon_list.viewport().width() <= 0:
            fallback = max(240, self._content_frame.width() - 24)
        relayout_icon_mode_list(
            self._icon_list,
            icon_size_preset_to_qsize(self._icon_size_preset),
            fallback_viewport_width=fallback,
            force=force,
        )

    def _apply_view_mode(self, mode: str) -> None:
        if mode not in {"icons", "list"}:
            return
        self._view_mode = mode
        self._last_device_view_sig = None
        self._persist_ui_prefs()
        self._sidebar_signature = None
        self._rebuild_sidebar()
        self._apply_stack_page()
        self._refresh_device_widgets()

    def _apply_arrange_mode(self, mode: str) -> None:
        if mode not in {"appearance", "sorted", "location"}:
            return
        self._icon_sort_mode = mode
        self._last_device_view_sig = None
        self._persist_ui_prefs()
        self._sidebar_signature = None
        self._rebuild_sidebar()
        self._refresh_device_widgets()

    def _persist_ui_prefs(self) -> None:
        prefs = load_ui_preferences()
        prefs["view_mode"] = self._view_mode
        prefs["icon_sort_mode"] = self._icon_sort_mode
        prefs["icon_size_preset"] = self._icon_size_preset
        prefs["selected_category"] = self._selected_category
        prefs["icon_group_expanded"] = dict(self._icon_group_expanded)
        sz = self._splitter.sizes()
        collapsed = bool(sz and len(sz) >= 2 and int(sz[0]) <= 8)
        prefs["sidebar_collapsed"] = collapsed
        if sz and int(sz[0]) > 8:
            self._sidebar_position = max(160, min(int(sz[0]), 480))
            prefs["sidebar_position"] = self._sidebar_position
        prefs["icon_source_overrides"] = {
            f"{ip}:{port}": mode for (ip, port), mode in self._icon_source_overrides.items()
        }
        prefs["custom_icon_overrides"] = {
            f"{ip}:{port}": cid for (ip, port), cid in self._custom_icon_overrides.items()
        }
        if self._discovery_manager is not None:
            prefs["device_commands"] = self._discovery_manager.get_device_commands_overrides()
            prefs["custom_command_overrides"] = self._discovery_manager.get_custom_command_overrides()
            prefs["field_mapping_rules"] = self._discovery_manager.get_field_mapping_rules()
            prefs["monitored_overrides"] = self._discovery_manager.get_monitored_overrides()
            prefs["hidden_overrides"] = self._discovery_manager.get_hidden_overrides()
            self._prune_hidden_device_meta()
            prefs["hidden_device_meta"] = dict(self._hidden_device_meta)
        save_ui_preferences(prefs)

    def _sync_menu_checks_from_state(self) -> None:
        if self._view_mode == "icons":
            self._act_display_icons.setChecked(True)
        else:
            self._act_display_list.setChecked(True)

        if self._icon_sort_mode == "appearance":
            self._act_arr_unsorted.setChecked(True)
        elif self._icon_sort_mode == "location":
            self._act_arr_location.setChecked(True)
        else:
            self._act_arr_type.setChecked(True)

        if self._icon_size_preset == "small":
            self._act_icon_sz_small.setChecked(True)
        elif self._icon_size_preset == "large":
            self._act_icon_sz_large.setChecked(True)
        elif self._icon_size_preset == "xlarge":
            self._act_icon_sz_xlarge.setChecked(True)
        else:
            self._act_icon_sz_medium.setChecked(True)

    def _apply_stack_page(self) -> None:
        if self._view_mode == "icons":
            self._stack.setCurrentWidget(self._icon_page_stack)
            self._sync_icon_page_stack_index()
        else:
            self._stack.setCurrentWidget(self._table)
        self._update_arrange_actions_enabled()
        self._sync_sidebar_top_spacer()
        QTimer.singleShot(0, self._sync_sidebar_top_spacer)

    def _sync_sidebar_top_spacer(self) -> None:
        self._sidebar_top_spacer.setFixedHeight(0)

    def _sync_icon_page_stack_index(self) -> None:
        if self._icon_sort_mode == "appearance":
            self._icon_page_stack.setCurrentWidget(self._icon_list)
        else:
            self._icon_page_stack.setCurrentWidget(self._grouped_icons_page)

    def _update_arrange_actions_enabled(self) -> None:
        arrange_en = True
        self._act_arr_unsorted.setEnabled(arrange_en)
        self._act_arr_type.setEnabled(arrange_en)
        self._act_arr_location.setEnabled(arrange_en)
        icon_sz_en = self._view_mode == "icons"
        self._act_view_menu_icon_size.setEnabled(icon_sz_en)

    def _quit_application(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _open_preferences(self) -> None:
        from ui.preferences_dialog import PreferencesDialog

        dlg = PreferencesDialog(
            self,
            on_location_presets_saved=self._apply_location_presets,
            on_type_presets_saved=self._apply_type_presets,
            on_connect_templates_saved=self._apply_connect_templates,
            on_clear_icon_cache=self._clear_icon_cache,
            on_icon_pack_changed=self._apply_icon_pack,
        )
        dlg.exec()

    def _apply_location_presets(self, options: list[str], _auto_add: bool) -> None:
        self._location_options = options

    def _apply_type_presets(self, options: list[tuple[str, str]]) -> None:
        self._type_options = options

    def _apply_connect_templates(self, templates: dict[str, str], custom: str) -> None:
        self._connect_command_templates = templates
        self._custom_command_template   = custom

    def _clear_icon_cache(self) -> None:
        from utils.device_remote_icon import clear_all_app_data
        clear_all_app_data()
        self._remote_icon_cache.clear()

    def _apply_icon_pack(self, pack_id: str) -> None:
        from utils.icon_packs import invalidate_icon_pack_cache
        from PySide6.QtGui import QPixmapCache
        invalidate_icon_pack_cache()
        QPixmapCache.clear()
        self._last_snapshot_fp = None  # force re-render even if devices unchanged
        self.set_devices(self._last_devices)

    def _reload_discovery(self) -> None:
        if self._discovery_manager is None:
            return
        self._discovery_manager.refresh()
        self.statusBar().showMessage(_("Discovery refresh requested"), 3000)

    def _show_about(self) -> None:
        from ui.about_dialog import show_about_dialog

        show_about_dialog(self)

    def _on_presence_transition(self, device: Device, kind: str) -> None:
        """Called from the discovery thread on online/offline transitions."""
        if not getattr(device, "monitored", False):
            return
        dt_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        name = str(device.name or device.ip)
        status = _("Online") if kind == "online" else _("Offline")
        self._notification_received.emit(dt_str, name, status)

    def _append_notification(self, dt_str: str, name: str, status: str) -> None:
        self._notification_log.append((dt_str, name, status))

    def _open_notifications_history(self) -> None:
        from ui.notifications_history_dialog import show_notifications_history_dialog

        def _clear() -> None:
            self._notification_log.clear()

        show_notifications_history_dialog(self, list(self._notification_log), _clear)

    def _prune_hidden_device_meta(self) -> None:
        if self._discovery_manager is None:
            return
        hidden = self._discovery_manager.get_hidden_overrides()
        self._hidden_device_meta = {
            key: meta
            for key, meta in self._hidden_device_meta.items()
            if hidden.get(key) is True
        }

    def _hidden_device_rows(self) -> list:
        from ui.hidden_devices_dialog import HiddenDeviceRow

        if self._discovery_manager is None:
            return []
        hidden = self._discovery_manager.get_hidden_overrides()
        rows: list[HiddenDeviceRow] = []
        seen: set[str] = set()
        for host_key, flagged in hidden.items():
            if not flagged or host_key in seen:
                continue
            seen.add(host_key)
            meta = self._hidden_device_meta.get(host_key)
            name = ""
            ip = ""
            port = 0
            if isinstance(meta, dict):
                name = str(meta.get("name", "") or "")
                ip = str(meta.get("ip", "") or "")
                try:
                    port = int(meta.get("port", 0) or 0)
                except (TypeError, ValueError):
                    port = 0
            if not name and ip:
                name = ip
            if not name:
                name = host_key
            rows.append(HiddenDeviceRow(host_key=host_key, name=name, ip=ip, port=port))
        rows.sort(key=lambda row: row.name.lower())
        return rows

    def _open_hidden_devices(self) -> None:
        from ui.hidden_devices_dialog import show_hidden_devices_dialog

        show_hidden_devices_dialog(
            self,
            self._hidden_device_rows(),
            self._unhide_device_by_key,
        )

    def _bundle_for_host_key(self, host_key: str) -> DeviceBundle | None:
        if self._discovery_manager is None or not host_key:
            return None
        for bundle in self._bundles:
            if self._discovery_manager.canonical_host_identity_key(bundle.ip, bundle.port) == host_key:
                return bundle
        return None

    def _apply_hidden_visibility_immediate(
        self,
        *,
        removed_bundle_key: str | None = None,
        added_bundle: DeviceBundle | None = None,
    ) -> None:
        """Refresh views after hide/unhide without rebuilding discovery bundles."""
        self._sidebar_signature = None
        self._last_device_view_sig = None
        if removed_bundle_key and self._try_incremental_remove_bundle(removed_bundle_key):
            self._rebuild_sidebar()
            self._update_visible_device_count_status()
            return
        if added_bundle is not None and self._try_incremental_add_bundle(added_bundle):
            self._rebuild_sidebar()
            self._update_visible_device_count_status()
            return
        self._rebuild_sidebar()
        self._refresh_device_widgets()

    def _try_incremental_remove_bundle(self, bundle_key: str) -> bool:
        if self._view_mode == "list":
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 0)
                if item is not None and item.data(self._BUNDLE_KEY_ROLE) == bundle_key:
                    self._table.removeRow(row)
                    return True
            return False
        if self._view_mode == "icons" and self._icon_sort_mode == "appearance":
            for index in range(self._icon_list.count()):
                item = self._icon_list.item(index)
                if item is not None and item.data(self._BUNDLE_KEY_ROLE) == bundle_key:
                    self._icon_list.takeItem(index)
                    self._relayout_flat_icon_list()
                    return True
        return False

    def _try_incremental_add_bundle(self, bundle: DeviceBundle) -> bool:
        ordered = self._current_ordered_bundles_for_view()
        try:
            insert_at = next(i for i, row in enumerate(ordered) if row.primary.key == bundle.primary.key)
        except StopIteration:
            return False
        if self._view_mode == "list":
            if self._table.isSortingEnabled():
                return False
            self._table.insertRow(insert_at)
            d = bundle.primary
            name_item = QTableWidgetItem(_safe_str(d.name))
            name_item.setData(self._BUNDLE_KEY_ROLE, d.key)
            self._table.setItem(insert_at, 0, name_item)
            self._table.setItem(insert_at, 1, QTableWidgetItem(_safe_str(d.ip)))
            self._table.setItem(insert_at, 2, QTableWidgetItem(format_device_type_for_details(d)))
            loc = bundle_location_label(bundle, no_location_label=self._no_location_label())
            self._table.setItem(insert_at, 3, QTableWidgetItem(loc))
            on_txt = _("Yes") if d.online else _("No")
            on_item = QTableWidgetItem(on_txt)
            on_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(insert_at, 4, on_item)
            self._table.resizeRowsToContents()
            return True
        if self._view_mode == "icons" and self._icon_sort_mode == "appearance":
            item = create_icon_tile_list_item(
                self._icon_for_bundle(bundle), _safe_str(bundle.primary.name)
            )
            item.setData(self._BUNDLE_KEY_ROLE, bundle.primary.key)
            item.setToolTip(self._bundle_tooltip(bundle))
            self._icon_list.insertItem(insert_at, item)
            self._relayout_flat_icon_list()
            return True
        return False

    def _unhide_device_by_key(self, host_key: str) -> None:
        if self._discovery_manager is None:
            return
        meta = self._hidden_device_meta.get(host_key)
        ip = ""
        port = 0
        if isinstance(meta, dict):
            ip = str(meta.get("ip", "") or "")
            try:
                port = int(meta.get("port", 0) or 0)
            except (TypeError, ValueError):
                port = 0
        if ip:
            self._discovery_manager.set_bundle_hidden(ip, port, False, notify=False)
        else:
            for device in list(self._discovery_manager.devices):
                if self._discovery_manager.canonical_host_identity_key(device.ip, device.port) == host_key:
                    self._discovery_manager.set_bundle_hidden(device.ip, device.port, False, notify=False)
                    break
            else:
                overrides = self._discovery_manager.get_hidden_overrides()
                overrides.pop(host_key, None)
                self._discovery_manager.set_hidden_overrides(overrides, notify=False)
        self._hidden_device_meta.pop(host_key, None)
        self._schedule_persist_ui_prefs()
        restored = self._bundle_for_host_key(host_key)
        self._apply_hidden_visibility_immediate(added_bundle=restored)

    def _hide_bundle(self, bundle: DeviceBundle) -> None:
        if self._discovery_manager is None:
            return
        host_key = self._discovery_manager.canonical_host_identity_key(bundle.ip, bundle.port)
        removed_key = bundle.primary.key
        self._discovery_manager.set_bundle_hidden(bundle.ip, bundle.port, True, notify=False)
        if host_key:
            self._hidden_device_meta[host_key] = {
                "name": bundle.name,
                "ip": bundle.ip,
                "port": int(bundle.port),
            }
        self._schedule_persist_ui_prefs()
        self._apply_hidden_visibility_immediate(removed_bundle_key=removed_key)

    def set_devices(self, devices: Iterable[Device]) -> None:
        """Replace views from discovery snapshot (coalesced to reduce flicker)."""
        self._pending_devices = list(devices)
        self._device_refresh_timer.stop()
        delay_ms = 120
        if not self._first_device_ui_flush_done and self._pending_devices:
            delay_ms = 0
        _LOG.debug(
            "set_devices: scheduled flush n=%s delay_ms=%s",
            len(self._pending_devices),
            delay_ms,
        )
        self._device_refresh_timer.start(delay_ms)

    def _flush_pending_devices(self) -> None:
        if self._pending_devices is None:
            return
        # Defer while a popup (context menu) is open — rebuilding would close it.
        _app = QApplication.instance()
        if _app is not None and _app.activePopupWidget() is not None:
            self._device_refresh_timer.start(400)
            return
        # Defer while the user is actively interacting (mouse moving, typing).
        if self._activity_guard.idle_ms() < 200:
            self._device_refresh_timer.start(250)
            return
        snapshot = self._pending_devices
        self._pending_devices = None
        _LOG.debug("_flush_pending_devices: applying n=%s", len(snapshot))
        self._apply_devices_snapshot(snapshot)
        if len(self._last_devices) > 0:
            self._first_device_ui_flush_done = True
            if self._scanning_overlay.isVisible():
                self._scanning_overlay.request_hide()

    def _apply_devices_snapshot(self, devices: list[Device]) -> None:
        """Apply a device list to bundles, sidebar, and main views (always merge like GTK)."""
        device_list = list(devices)
        device_fp = _device_snapshot_ui_fingerprint(device_list)
        new_fp_prefix = (tuple(self._information_precedence), device_fp)
        if (
            isinstance(self._last_snapshot_fp, tuple)
            and len(self._last_snapshot_fp) >= 2
            and self._last_snapshot_fp[:2] == new_fp_prefix
            and self._last_device_view_sig is not None
        ):
            _LOG.debug("_apply_devices_snapshot: skipped (fingerprint unchanged) n=%s", len(device_list))
            return
        self._last_devices = device_list
        self._bundles = build_device_bundles(device_list, self._information_precedence)
        self._last_snapshot_fp = new_fp_prefix + (bundle_snapshot_ui_fingerprint(self._bundles),)
        _LOG.debug("_apply_devices_snapshot: n=%s", len(device_list))
        self._maybe_auto_add_discovered_locations()
        self._rebuild_sidebar()
        self._last_device_view_sig = None
        self._schedule_view_refresh()

    def _maybe_auto_add_discovered_locations(self) -> None:
        prefs = load_ui_preferences()
        if not prefs.get("auto_add_discovered_locations", False):
            return
        discovered: list[str] = []
        for bundle in self._bundles:
            for device in bundle.devices:
                md = device.metadata if isinstance(device.metadata, dict) else {}
                loc = md.get("user_location")
                if isinstance(loc, str) and loc.strip():
                    discovered.append(loc.strip())
        if not discovered:
            return
        merged = normalize_location_options(self._location_options + discovered)
        if merged == self._location_options:
            return
        self._location_options = merged
        prefs["location_options"] = merged
        save_ui_preferences(prefs)

    def _filtered_bundles(self) -> list[DeviceBundle]:
        bundles = apply_bundle_category_filter(
            self._bundles,
            self._selected_category,
            no_location_label=self._no_location_label(),
        )
        if self._discovery_manager is None:
            return bundles
        return [
            bundle
            for bundle in bundles
            if not self._discovery_manager.bundle_is_hidden(bundle.ip, bundle.port)
        ]

    def _current_ordered_bundles_for_view(self) -> list[DeviceBundle]:
        filtered = self._filtered_bundles()
        return _ordered_bundles(
            filtered,
            self._icon_sort_mode,
            no_location_label=self._no_location_label(),
        )

    def _device_view_refresh_signature(self, ordered: list[DeviceBundle]) -> tuple:
        """Detect duplicate discovery snapshots so we can skip rebuilding native icon widgets."""
        return (
            self._view_mode,
            self._icon_sort_mode,
            self._icon_size_preset,
            self._selected_category,
            tuple(
                (
                    b.primary.key,
                    _safe_str(b.primary.name),
                    _safe_str(b.primary.ip),
                    (b.primary.type or "").lower(),
                    bundle_location_label(b, no_location_label=self._no_location_label()),
                    bool(b.primary.online),
                )
                for b in ordered
            ),
        )

    def _refresh_device_widgets(self) -> None:
        ordered_for_sig = self._current_ordered_bundles_for_view()
        sig = self._device_view_refresh_signature(ordered_for_sig)
        if sig == self._last_device_view_sig:
            _LOG.debug(
                "_refresh_device_widgets: skipped (unchanged snapshot) rows=%s",
                len(ordered_for_sig),
            )
            return
        _LOG.debug(
            "_refresh_device_widgets: view=%s arrange=%s icon_preset=%s rows=%s",
            self._view_mode,
            self._icon_sort_mode,
            self._icon_size_preset,
            len(ordered_for_sig),
        )
        to_freeze = [
            self.centralWidget(),
            self._splitter,
            self._stack,
            self._icon_page_stack,
            self._grouped_icons_page,
            self._icon_list,
            self._table,
        ]
        for w in to_freeze:
            if w is not None:
                w.setUpdatesEnabled(False)
        try:
            table_rows = self._filtered_bundles() if self._view_mode == "list" else ordered_for_sig
            self._fill_table(table_rows)
            if self._view_mode == "icons":
                self._sync_icon_page_stack_index()
                if self._icon_sort_mode == "appearance":
                    self._clear_grouped_icons_page()
                    self._fill_flat_icon_list(ordered_for_sig)
                else:
                    self._rebuild_grouped_icon_page(ordered_for_sig)
            else:
                self._clear_grouped_icons_page()
        finally:
            for w in reversed(to_freeze):
                if w is not None:
                    w.setUpdatesEnabled(True)
        self._last_device_view_sig = sig
        self.statusBar().showMessage(_("{} devices").format(len(table_rows)))
        if os.environ.get("NETNEIGHBOR_DEBUG_TOPLEVEL") and self._view_mode == "icons":
            _debug_log_extra_top_level_widgets("_refresh_device_widgets done")

    def _bundle_for_primary_key(self, key: object) -> DeviceBundle | None:
        if not isinstance(key, str) or not key:
            return None
        for b in self._filtered_bundles():
            if b.primary.key == key:
                return b
        return None

    def _on_icon_list_context_menu(self, pos: QPoint) -> None:
        it = self._icon_list.itemAt(pos)
        if it is None:
            return
        key = it.data(self._BUNDLE_KEY_ROLE)
        bundle = self._bundle_for_primary_key(key)
        if bundle is None:
            return
        gpos = self._icon_list.viewport().mapToGlobal(pos)
        self._show_device_context_menu(gpos, bundle)

    def _on_table_context_menu(self, pos: QPoint) -> None:
        idx = self._table.indexAt(pos)
        if not idx.isValid():
            return
        item = self._table.item(idx.row(), 0)
        if item is None:
            return
        key = item.data(self._BUNDLE_KEY_ROLE)
        bundle = self._bundle_for_primary_key(key)
        if bundle is None:
            return
        gpos = self._table.viewport().mapToGlobal(pos)
        self._show_device_context_menu(gpos, bundle)

    def _on_grouped_icon_context_menu(self, global_pos: QPoint, bundle: DeviceBundle) -> None:
        self._show_device_context_menu(global_pos, bundle)

    def _on_tile_double_clicked(self, bundle: DeviceBundle) -> None:
        uri = resolve_connect_target(
            bundle_ip=bundle.ip,
            primary_type=bundle.primary.type or "",
            devices=bundle.devices,
        )
        if uri:
            launch_open_uri(
                self, bundle, uri,
                connect_templates=self._connect_command_templates,
                global_custom_command=self._custom_command_template or "",
            )

    def _on_icon_list_double_clicked(self, item) -> None:
        key = item.data(self._BUNDLE_KEY_ROLE)
        bundle = self._bundle_for_primary_key(key)
        if bundle is not None:
            self._on_tile_double_clicked(bundle)

    def _on_table_double_clicked(self, row: int, _col: int) -> None:
        item = self._table.item(row, 0)
        if item is None:
            return
        bundle = self._bundle_for_primary_key(item.data(self._BUNDLE_KEY_ROLE))
        if bundle is not None:
            self._on_tile_double_clicked(bundle)

    def _bundle_connect_targets(self, bundle: DeviceBundle) -> list[tuple[str, str]]:
        return resolve_all_connect_targets(
            bundle_ip=bundle.ip,
            primary_type=bundle.primary.type or "",
            devices=bundle.devices,
        )

    def _show_device_context_menu(self, global_pos: QPoint, bundle: DeviceBundle) -> None:
        has_cmd = bool(
            bundle_custom_command(bundle) or (self._custom_command_template or "").strip()
        )
        current_loc = bundle_location_label(bundle, no_location_label="")
        if current_loc and current_loc not in self._location_options:
            loc_opts = normalize_location_options(self._location_options + [current_loc])
        else:
            loc_opts = self._location_options
        show_device_context_menu(
            self,
            global_pos,
            bundle,
            connect_targets=self._bundle_connect_targets(bundle),
            location_options=loc_opts,
            type_options=self._type_options,
            has_custom_command=has_cmd,
            on_open_uri=lambda uri: launch_open_uri(
                self,
                bundle,
                uri,
                connect_templates=self._connect_command_templates,
                global_custom_command=self._custom_command_template,
            ),
            on_custom_command=lambda: run_custom_command_for_bundle(
                self,
                bundle,
                global_template=self._custom_command_template,
                connect_templates=self._connect_command_templates,
            ),
            on_details=lambda: self._open_device_details(bundle),
            on_options=lambda: self._open_device_details(bundle, initial_tab="options"),
            on_monitor=lambda monitored: self._set_bundle_monitored(bundle, monitored),
            on_hide=lambda: self._hide_bundle(bundle),
            on_rename=lambda: self._rename_bundle(bundle),
            on_location=lambda loc: self._set_bundle_location(bundle, loc),
            on_type=lambda slug: self._set_bundle_type(bundle, slug),
        )

    def _normalized_icon_mode(self, bundle: DeviceBundle) -> str:
        raw = self._icon_source_overrides.get((bundle.ip, bundle.port), "provided")
        if raw == "auto":
            return "provided"
        if raw in {"provided", "system", "custom"}:
            return raw
        return "provided"

    def _apply_bundle_icon_settings(
        self, bundle: DeviceBundle, mode: str, custom_icon_id: str | None
    ) -> None:
        ep = (bundle.ip, bundle.port)
        current = self._normalized_icon_mode(bundle)
        if mode == current:
            if mode != "custom":
                if ep in self._custom_icon_overrides:
                    self._custom_icon_overrides.pop(ep, None)
                    self._persist_ui_prefs()
                    self._last_device_view_sig = None
                    self._refresh_device_widgets()
                return
            prev = self._custom_icon_overrides.get(ep)
            if custom_icon_id and prev != custom_icon_id:
                self._custom_icon_overrides[ep] = custom_icon_id
                self._persist_ui_prefs()
                self._last_device_view_sig = None
                self._refresh_device_widgets()
            return
        self._icon_source_overrides[ep] = mode
        if mode == "custom":
            if custom_icon_id:
                self._custom_icon_overrides[ep] = custom_icon_id
        else:
            self._custom_icon_overrides.pop(ep, None)
        self._persist_ui_prefs()
        self._last_device_view_sig = None
        self._refresh_device_widgets()

    def _open_device_details(
        self, bundle: DeviceBundle, *, initial_tab: str | None = None
    ) -> None:
        model = build_device_details_view_model(
            bundle, no_location_label=self._no_location_label()
        )
        ep = (bundle.ip, bundle.port)
        mode = self._normalized_icon_mode(bundle)
        custom_id = self._custom_icon_overrides.get(ep) if mode == "custom" else None

        def _pick_custom() -> str | None:
            cur = self._custom_icon_overrides.get(ep) if mode == "custom" else None
            return pick_device_icon_id(
                self, preferred_type=bundle.primary.type, current_id=cur
            )

        provided_pixmap = None
        provided_native_size = None
        for dev in (bundle.ssdp_device, bundle.wsdd_device, bundle.wsd_device,
                    bundle.nmb_device, bundle.mdns_device):
            if dev is None:
                continue
            raw_bytes = self._remote_icon_cache.bytes_for_device(dev)
            if raw_bytes:
                from ui.remote_icon_cache import pixmap_from_icon_bytes
                pix = pixmap_from_icon_bytes(raw_bytes, 128)
                if pix is not None and not pix.isNull():
                    provided_native_size = (pix.width(), pix.height())
                    provided_pixmap = pix
                break

        icon_devices = [
            dev for dev in (bundle.ssdp_device, bundle.wsdd_device, bundle.wsd_device,
                            bundle.nmb_device, bundle.mdns_device)
            if dev is not None
        ]
        icon_settings = DeviceIconSettings(
            icon_mode=mode,
            has_device_icon_source=bundle_has_device_icon_source(bundle),
            provided_icon_display=bundle_provided_icon_display(bundle),
            selected_custom_icon_id=custom_id,
            on_apply=lambda m, cid: self._apply_bundle_icon_settings(bundle, m, cid),
            on_pick_custom=_pick_custom,
            provided_icon_pixmap=provided_pixmap,
            provided_icon_native_size=provided_native_size,
            icon_cache=self._remote_icon_cache,
            icon_devices=icon_devices,
        )

        command_settings: DeviceCommandSettings | None = None
        if self._discovery_manager is not None:
            # Merge legacy custom_command into device_commands list as a "custom" override entry
            initial_cmds = list(model.device_commands or [])
            has_custom_override = any(
                isinstance(c, dict) and c.get("scheme") == "custom" and c.get("mode") == "override"
                for c in initial_cmds
            )
            if not has_custom_override and model.custom_command:
                initial_cmds = [
                    {"scheme": "custom", "ip": model.custom_command, "port": 0,
                     "mode": "override", "label": ""}
                ] + initial_cmds

            def _on_set_device_commands(cmds: list[dict]) -> None:
                if self._discovery_manager is None:
                    return
                for dev in bundle.devices:
                    self._discovery_manager.set_device_commands(
                        dev.source, dev.ip, dev.port, cmds
                    )
                    # Keep legacy custom_command in sync for backward compat
                    custom_override = next(
                        (c for c in cmds
                         if isinstance(c, dict)
                         and c.get("scheme") == "custom"
                         and c.get("mode") == "override"),
                        None,
                    )
                    legacy_tmpl = str(custom_override.get("ip", "")).strip() if custom_override else None
                    self._discovery_manager.set_device_custom_command(
                        dev.source, dev.ip, dev.port, legacy_tmpl or None
                    )
                self._persist_ui_prefs()

            command_settings = DeviceCommandSettings(
                device_commands=initial_cmds,
                on_set_device_commands=_on_set_device_commands,
            )

        field_rule_cb = None
        field_rules: dict[str, list[str]] | None = None
        delete_rule_cb = None
        if self._discovery_manager is not None:
            def _on_set_field_rule(target: str, field_path: str) -> None:
                if self._discovery_manager is None:
                    return
                for dev in bundle.devices:
                    self._discovery_manager.remove_device_field_mapping_rule(
                        dev.source, dev.ip, dev.port, target
                    )
                    self._discovery_manager.set_device_field_mapping_rule(
                        dev.source, dev.ip, dev.port, target, field_path
                    )
                self._persist_ui_prefs()
            field_rule_cb = _on_set_field_rule

            fr = self._discovery_manager.get_device_field_mapping_rules(bundle.primary)
            if fr:
                field_rules = fr

            def _on_delete_field_rule(target: str, field_path: str) -> None:
                if self._discovery_manager is None:
                    return
                for dev in bundle.devices:
                    self._discovery_manager.remove_device_field_mapping_rule(
                        dev.source, dev.ip, dev.port, target, field_path
                    )
                self._persist_ui_prefs()
            delete_rule_cb = _on_delete_field_rule

        show_device_details_dialog(
            self,
            model,
            initial_tab=initial_tab,
            icon_settings=icon_settings,
            command_settings=command_settings,
            on_set_field_rule=field_rule_cb,
            field_rules=field_rules,
            on_delete_field_rule=delete_rule_cb,
        )

    def _set_bundle_monitored(self, bundle: DeviceBundle, monitored: bool) -> None:
        if self._discovery_manager is None:
            return
        self._discovery_manager.set_bundle_monitored(bundle.ip, bundle.port, monitored)
        self._persist_ui_prefs()

    def _rename_bundle(self, bundle: DeviceBundle) -> None:
        if self._discovery_manager is None:
            return
        result = prompt_rename_device(self, bundle.name)
        if result is False:
            return
        for device in bundle.devices:
            self._discovery_manager.set_device_name_override(
                device.source, device.ip, device.port, result
            )
        self._persist_ui_prefs()

    def _set_bundle_location(self, bundle: DeviceBundle, location: str | None) -> None:
        if self._discovery_manager is None:
            return
        for device in bundle.devices:
            self._discovery_manager.set_device_location_override(
                device.source, device.ip, device.port, location
            )
        self._persist_ui_prefs()
        self._sidebar_signature = None
        self._rebuild_sidebar()
        self._last_device_view_sig = None
        self._refresh_device_widgets()

    def _set_bundle_type(self, bundle: DeviceBundle, device_type: str | None) -> None:
        if self._discovery_manager is None:
            return
        self._discovery_manager.set_device_type_override(
            bundle.primary.source, bundle.primary.ip, bundle.primary.port, device_type
        )
        self._persist_ui_prefs()
        self._sidebar_signature = None
        self._rebuild_sidebar()
        self._last_device_view_sig = None
        self._refresh_device_widgets()

    def _icon_for_bundle(self, bundle: DeviceBundle) -> QIcon:
        ep = (bundle.ip, bundle.port)
        mode = self._normalized_icon_mode(bundle)
        px = ICON_SIZE_PRESET_PIXELS.get(
            normalize_icon_size_preset(self._icon_size_preset),
            DEVICE_ICON_REFERENCE_PX,
        )

        if mode == "custom":
            cid = self._custom_icon_overrides.get(ep)
            if cid:
                p = resolve_persisted_icon_id_to_path(cid)
                if p is not None:
                    ic = QIcon(str(p))
                    if not ic.isNull():
                        return ic
            mode = "provided"

        if mode == "provided":
            for dev in (
                bundle.ssdp_device,
                bundle.wsdd_device,
                bundle.wsd_device,
                bundle.nmb_device,
                bundle.mdns_device,
            ):
                if dev is None:
                    continue
                remote = self._remote_icon_cache.icon_for_bundle_device(
                    bundle, dev, px
                )
                if remote is not None and not remote.isNull():
                    return remote

        return self._default_icon_for_bundle(bundle)

    def _default_icon_for_bundle(self, bundle: DeviceBundle) -> QIcon:
        """Type icons from the 48px-based asset pack; avoid upscaling tiny device PNGs."""
        ic = qt_icon_for_device_type(
            bundle.primary.type,
            self.style(),
            self,
            icon_size_preset=self._icon_size_preset,
        )
        if not ic.isNull():
            return ic
        asset = resolve_asset_icon_file(bundle.primary.icon)
        if asset is not None:
            return QIcon(str(asset))
        return ic

    def _on_remote_icons_ready(self) -> None:
        self._resync_device_icons()

    def _resync_device_icons(self) -> None:
        if self._view_mode != "icons":
            return
        if self._icon_sort_mode == "appearance":
            self._resync_flat_icon_list_pixmaps()
            return
        layout = self._grouped_icons_layout
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item is None:
                continue
            scroll = item.widget()
            if scroll is None:
                continue
            sections = getattr(scroll, "_icon_group_sections", None)
            if not isinstance(sections, list):
                continue
            for section in sections:
                section.resync_icons(self._icon_for_bundle)

    def _apply_list_table_sort_for_arrange_mode(self) -> None:
        """List view: match View → Arrange to sorting by IP / Type / Location column (like header clicks)."""
        if self._view_mode != "list":
            return
        hdr = self._table.horizontalHeader()
        mode = self._icon_sort_mode
        if mode == "appearance":
            col = 1
        elif mode == "sorted":
            col = 2
        elif mode == "location":
            col = 3
        else:
            col = 1
        self._table_sort_programmatic = True
        hdr.blockSignals(True)
        try:
            self._table.setSortingEnabled(False)
            self._table.setSortingEnabled(True)
            self._table.sortByColumn(col, Qt.SortOrder.AscendingOrder)
        finally:
            hdr.blockSignals(False)
            self._table_sort_programmatic = False

    def _on_table_sort_indicator_changed(self, logical_index: int, order: Qt.SortOrder) -> None:
        if self._view_mode != "list" or self._table_sort_programmatic:
            return
        if logical_index == 1:
            mode = "appearance"
        elif logical_index == 2:
            mode = "sorted"
        elif logical_index == 3:
            mode = "location"
        else:
            return
        if mode == self._icon_sort_mode:
            return
        self._icon_sort_mode = mode
        self._persist_ui_prefs()
        self._sidebar_signature = None
        self._rebuild_sidebar()
        self._sync_menu_checks_from_state()

    def _fill_table(self, rows: list[DeviceBundle]) -> None:
        saved_scroll = self._table.verticalScrollBar().value()
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for row, bundle in enumerate(rows):
            d = bundle.primary
            name_item = QTableWidgetItem(_safe_str(d.name))
            name_item.setData(self._BUNDLE_KEY_ROLE, d.key)
            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, QTableWidgetItem(_safe_str(d.ip)))
            self._table.setItem(row, 2, QTableWidgetItem(format_device_type_for_details(d)))
            loc = bundle_location_label(bundle, no_location_label=self._no_location_label())
            self._table.setItem(row, 3, QTableWidgetItem(loc))
            on_txt = _("Yes") if d.online else _("No")
            on_item = QTableWidgetItem(on_txt)
            on_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 4, on_item)
        self._table.resizeRowsToContents()
        if self._view_mode == "list":
            self._apply_list_table_sort_for_arrange_mode()
        else:
            self._table.setSortingEnabled(False)
        if saved_scroll > 0:
            self._table.verticalScrollBar().setValue(saved_scroll)

    def _bundle_tooltip(self, bundle: DeviceBundle) -> str:
        d = bundle.primary
        loc = bundle_location_label(bundle, no_location_label=self._no_location_label())
        parts = [_safe_str(d.ip), format_device_type_for_details(d)]
        if loc:
            parts.append(loc)
        last_seen_str = _format_last_seen(d.last_seen)
        if last_seen_str:
            parts.append(last_seen_str)
        return "\n".join(parts)

    def _run_flat_icon_list_batched(self, work: Callable[[], None]) -> None:
        """Batch list mutations without disabling the widget (avoids stray Windows taskbar entries)."""
        lst = self._icon_list
        vp = lst.viewport()
        lst.blockSignals(True)
        vp.setUpdatesEnabled(False)
        lst.setUpdatesEnabled(False)
        try:
            work()
        finally:
            lst.setUpdatesEnabled(True)
            vp.setUpdatesEnabled(True)
            lst.blockSignals(False)

    def _fill_flat_icon_list(self, rows: list[DeviceBundle]) -> None:
        saved_scroll = self._icon_list.verticalScrollBar().value()

        def _fill() -> None:
            self._icon_list.clear()
            for bundle in rows:
                it = create_icon_tile_list_item(
                    self._icon_for_bundle(bundle), _safe_str(bundle.primary.name)
                )
                it.setData(self._BUNDLE_KEY_ROLE, bundle.primary.key)
                it.setToolTip(self._bundle_tooltip(bundle))
                self._icon_list.addItem(it)

        self._run_flat_icon_list_batched(_fill)
        self._relayout_flat_icon_list(force=True)
        if saved_scroll > 0:
            QTimer.singleShot(0, lambda: self._icon_list.verticalScrollBar().setValue(saved_scroll))

    def _resync_flat_icon_list_pixmaps(self) -> None:
        """Refresh icons after preset change without rebuilding the whole list."""
        ordered = self._current_ordered_bundles_for_view()
        by_key = {b.primary.key: b for b in ordered}

        def _sync() -> None:
            for i in range(self._icon_list.count()):
                it = self._icon_list.item(i)
                raw = it.data(self._BUNDLE_KEY_ROLE)
                if not isinstance(raw, str):
                    continue
                bundle = by_key.get(raw)
                if bundle is None:
                    continue
                it.setIcon(self._icon_for_bundle(bundle))
                set_icon_tile_item_label(it, _safe_str(bundle.primary.name))
                it.setToolTip(self._bundle_tooltip(bundle))

        self._run_flat_icon_list_batched(_sync)
        self._relayout_flat_icon_list()

    def _on_icon_section_toggled(self, group_id: str, expanded: bool) -> None:
        self._icon_group_expanded[group_id] = expanded
        self._persist_ui_prefs()

    def _clear_grouped_icons_page(self) -> None:
        while self._grouped_icons_layout.count():
            item = self._grouped_icons_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _rebuild_grouped_icon_page(self, ordered: list[DeviceBundle]) -> None:
        page = self._grouped_icons_page
        was_page_visible = not page.isHidden()

        saved_scroll = 0
        if self._grouped_icons_layout.count():
            existing = self._grouped_icons_layout.itemAt(0)
            if existing is not None and existing.widget() is not None:
                saved_scroll = existing.widget().verticalScrollBar().value()

        new_scroll = None
        page.setVisible(False)
        try:
            self._clear_grouped_icons_page()

            mode = self._icon_sort_mode
            if mode not in {"sorted", "location"}:
                return

            new_scroll = build_grouped_icon_scroll(
                page,
                mode=mode,
                ordered_bundles=ordered,
                no_location_label=self._no_location_label(),
                expanded_map=self._icon_group_expanded,
                icon_for_bundle=self._icon_for_bundle,
                tooltip_for_bundle=self._bundle_tooltip,
                icon_size=icon_size_preset_to_qsize(self._icon_size_preset),
                on_section_toggled=self._on_icon_section_toggled,
                on_tile_context_menu=self._on_grouped_icon_context_menu,
                on_tile_double_clicked=self._on_tile_double_clicked,
            )
            self._grouped_icons_layout.addWidget(new_scroll)
        finally:
            page.setVisible(was_page_visible)

        if new_scroll is not None:
            _sections = getattr(new_scroll, "_icon_group_sections", [])
            if _sections:
                QTimer.singleShot(0, lambda: [s._relayout_icon_grid() for s in _sections])

        if saved_scroll > 0 and new_scroll is not None:
            QTimer.singleShot(0, lambda: new_scroll.verticalScrollBar().setValue(saved_scroll))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if (
            event.type() == QEvent.Type.WindowStateChange
            and self.isMinimized()
            and QSystemTrayIcon.isSystemTrayAvailable()
        ):
            event.ignore()
            QTimer.singleShot(0, self.hide)

    def closeEvent(self, event) -> None:
        from utils.ui_prefs import load_ui_preferences
        prefs = load_ui_preferences()
        if prefs.get("close_to_tray", True) and QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
        else:
            event.accept()

    def bring_to_front(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()


def _debug_log_extra_top_level_widgets(context: str) -> None:
    """Log unexpected Qt top-level surfaces (NETNEIGHBOR_DEBUG_TOPLEVEL=1 or ``--qt-diag-toplevels``)."""
    if not os.environ.get("NETNEIGHBOR_DEBUG_TOPLEVEL"):
        return
    app = QApplication.instance()
    if app is None:
        _LOG.info("TLW diag [%s]: no QApplication", context)
        return
    extras: list[str] = []
    for w in app.topLevelWidgets():
        if not w.isWindow():
            continue
        mo = w.metaObject()
        cn = mo.className() if mo is not None else type(w).__name__
        if cn == "NetNeighborMainWindow":
            continue
        extras.append(f"{cn}(title={w.windowTitle()!r},visible={w.isVisible()})")

    qwin_parts: list[str] = []
    try:
        from PySide6.QtGui import QGuiApplication

        ga = QGuiApplication.instance()
        if ga is not None:
            for qw in ga.topLevelWindows():
                if qw is None:
                    continue
                qwin_parts.append(f"{type(qw).__name__}(title={qw.title()!r},vis={qw.isVisible()})")
    except Exception as e:
        qwin_parts.append(f"<error: {e}>")

    _LOG.info(
        "TLW diag [%s]: QWidget_windows_extras=%s | QGuiApplication.topLevelWindows=%s",
        context,
        len(extras),
        len(qwin_parts),
    )
    if extras:
        _LOG.info("TLW diag [%s] QWidget extras: %s", context, " | ".join(extras))
    if qwin_parts:
        _LOG.info("TLW diag [%s] QWindows: %s", context, " | ".join(qwin_parts))


def _disabled_title_action(parent: QMainWindow, text: str) -> QAction:
    """Section label in menu (non-clickable)."""
    a = QAction(text, parent)
    a.setEnabled(False)
    return a


def _format_last_seen(last_seen: datetime | None) -> str:
    """Return a human-readable 'Last seen: X ago' string, or '' if seen very recently."""
    if last_seen is None:
        return ""
    try:
        now = datetime.now(timezone.utc)
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        delta_s = (now - last_seen).total_seconds()
        if delta_s < 120:
            return ""  # seen in the last 2 min → no label needed
        if delta_s < 3600:
            mins = int(delta_s // 60)
            return _("Last seen: {n} min ago").format(n=mins)
        if delta_s < 86400:
            hrs = int(delta_s // 3600)
            return _("Last seen: {n} h ago").format(n=hrs)
        days = int(delta_s // 86400)
        return _("Last seen: {n} d ago").format(n=days)
    except Exception:
        return ""


def _device_snapshot_ui_fingerprint(devices: Iterable[Device]) -> tuple:
    """Stable fingerprint of fields that affect the Qt table and icon labels (not raw metadata noise)."""
    rows: list[tuple] = []
    for d in sorted(devices, key=lambda x: x.key):
        md = d.metadata if isinstance(d.metadata, dict) else {}
        rows.append(
            (
                d.key,
                (d.source or "").strip().lower(),
                (d.name or "").strip(),
                (d.ip or "").strip(),
                int(d.port or 0),
                (d.type or "unknown").strip().lower(),
                bool(d.online),
                (md.get("user_location") or "").strip(),
            )
        )
    return (len(rows), tuple(rows))


def _ordered_bundles(
    bundles: list[DeviceBundle],
    icon_sort_mode: str,
    *,
    no_location_label: str,
) -> list[DeviceBundle]:
    rows = list(bundles)
    if icon_sort_mode == "appearance":
        return rows
    if icon_sort_mode == "sorted":
        return sorted(
            rows,
            key=lambda b: (b.primary.category.lower(), b.primary.type.lower(), b.primary.name.lower()),
        )
    if icon_sort_mode == "location":
        return sorted(
            rows,
            key=lambda b: (
                bundle_location_label(b, no_location_label=no_location_label).lower(),
                b.primary.category.lower(),
                b.primary.name.lower(),
            ),
        )
    return rows


def _safe_str(value: object) -> str:
    s = str(value) if value is not None else ""
    return s.strip() or "-"
