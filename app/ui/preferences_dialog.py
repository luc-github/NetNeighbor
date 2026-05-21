# File preferences_dialog.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Preferences dialog — General, Notifications, Locations, Types, Applications."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from gettext import gettext as _

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QListWidget,
    QVBoxLayout,
    QWidget,
)

from utils.connect_launcher import default_connect_command_templates, normalize_connect_templates
from utils.location_label import normalize_location_options
from utils.session_autostart import apply_autostart_pref, autostart_enabled_on_disk
from utils.ui_prefs import load_ui_preferences, save_ui_preferences

from ui.preset_editors import (
    _TypeEntryDialog,
    _LIST_QSS,
    _TREE_QSS,
    _SCHEME_ROWS,
    _default_location_presets,
    _default_type_options,
)


class PreferencesDialog(QDialog):
    """Modal preferences with tabs: General, Notifications, Locations, Types, Applications."""

    _THEME_QSS = (
        "QPushButton#nnThemeL, QPushButton#nnThemeM, QPushButton#nnThemeR {"
        " background: palette(button); color: palette(text);"
        " border: 1px solid palette(mid); border-right: none; border-radius: 0px;"
        " padding: 6px 0px; font-size: 14px;"
        "}"
        "QPushButton#nnThemeL {"
        " border-top-left-radius: 4px; border-bottom-left-radius: 4px;"
        "}"
        "QPushButton#nnThemeR {"
        " border-right: 1px solid palette(mid);"
        " border-top-right-radius: 4px; border-bottom-right-radius: 4px;"
        "}"
        "QPushButton#nnThemeL:checked, QPushButton#nnThemeM:checked, QPushButton#nnThemeR:checked {"
        " background: palette(highlight); color: palette(highlighted-text);"
        " border-color: palette(highlight);"
        "}"
        "QPushButton#nnThemeL:hover:!checked, QPushButton#nnThemeM:hover:!checked,"
        "QPushButton#nnThemeR:hover:!checked {"
        " background: palette(alternate-base);"
        "}"
    )

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        on_location_presets_saved: Callable[[list[str], bool], None] | None = None,
        on_type_presets_saved: Callable[[list[tuple[str, str]]], None] | None = None,
        on_connect_templates_saved: Callable[[dict[str, str], str], None] | None = None,
        on_clear_icon_cache: Callable[[], None] | None = None,
        on_icon_pack_changed: Callable[[str], None] | None = None,
        on_restart: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Preferences"))
        self.setModal(True)
        self.resize(580, 520)
        self._on_location_presets_saved  = on_location_presets_saved
        self._on_type_presets_saved      = on_type_presets_saved
        self._on_connect_templates_saved = on_connect_templates_saved
        self._on_clear_icon_cache        = on_clear_icon_cache
        self._on_icon_pack_changed       = on_icon_pack_changed
        self._on_restart                 = on_restart
        self._icon_packs: list = []
        self._icon_folder_opened: bool = False

        self._prefs = load_ui_preferences()
        self._original_theme = str(self._prefs.get("theme", "auto"))

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(),       _("General"))
        tabs.addTab(self._build_notifications_tab(), _("Notifications"))
        tabs.addTab(self._build_locations_tab(),     _("Locations"))
        tabs.addTab(self._build_types_tab(),         _("Types"))
        tabs.addTab(self._build_applications_tab(),  _("Applications"))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_save)
        buttons.rejected.connect(self._cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

        self._load_widgets()

    # ------------------------------------------------------------------
    # Tab builders
    # ------------------------------------------------------------------

    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        theme_box = QGroupBox(_("Theme"))
        theme_inner = QWidget()
        theme_inner.setStyleSheet(self._THEME_QSS)
        tl = QHBoxLayout(theme_inner)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(0)
        self._btn_theme_light = QPushButton("☀  " + _("Light"))
        self._btn_theme_auto  = QPushButton("⊙  " + _("Auto"))
        self._btn_theme_dark  = QPushButton("☾  " + _("Dark"))
        self._btn_theme_light.setObjectName("nnThemeL")
        self._btn_theme_auto.setObjectName("nnThemeM")
        self._btn_theme_dark.setObjectName("nnThemeR")
        self._theme_group = QButtonGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_group.addButton(self._btn_theme_light, 0)
        self._theme_group.addButton(self._btn_theme_auto,  1)
        self._theme_group.addButton(self._btn_theme_dark,  2)
        for btn in (self._btn_theme_light, self._btn_theme_auto, self._btn_theme_dark):
            btn.setCheckable(True)
            btn.setMinimumHeight(34)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            tl.addWidget(btn)
        self._theme_group.idToggled.connect(self._on_theme_toggled)
        QVBoxLayout(theme_box).addWidget(theme_inner)

        icon_pack_box = QGroupBox(_("Icon pack"))
        ip_outer = QVBoxLayout(icon_pack_box)
        ip_top = QHBoxLayout()
        self._icon_pack_combo = QComboBox()
        self._icon_pack_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._icon_pack_combo.currentIndexChanged.connect(self._on_icon_pack_combo_changed)
        btn_open_packs = QPushButton(_("Open folder…"))
        btn_open_packs.setFixedWidth(110)
        btn_open_packs.clicked.connect(self._on_open_icon_packs_folder)
        ip_top.addWidget(self._icon_pack_combo)
        ip_top.addWidget(btn_open_packs)
        self._icon_pack_scroll = QScrollArea()
        self._icon_pack_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._icon_pack_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._icon_pack_scroll.setWidgetResizable(True)
        self._icon_pack_scroll.setFixedHeight(72)
        self._icon_pack_scroll.setStyleSheet(
            "QScrollArea { border: 1px solid palette(mid); border-radius: 4px; background: palette(base); }"
        )
        self._icon_pack_strip_widget = QWidget()
        self._icon_pack_strip_layout = QHBoxLayout(self._icon_pack_strip_widget)
        self._icon_pack_strip_layout.setContentsMargins(4, 4, 4, 4)
        self._icon_pack_strip_layout.setSpacing(4)
        self._icon_pack_scroll.setWidget(self._icon_pack_strip_widget)
        ip_outer.addLayout(ip_top)
        ip_outer.addWidget(self._icon_pack_scroll)

        session = QGroupBox(_("Session"))
        sl = QVBoxLayout(session)
        self._close_to_tray   = QCheckBox(_("Close to system tray / panel instead of exiting"))
        self._start_minimized = QCheckBox(_("Start minimized to tray"))
        self._start_at_login  = QCheckBox(_("Start NetNeighbor when logging in"))
        sl.addWidget(self._close_to_tray)
        sl.addWidget(self._start_minimized)
        sl.addWidget(self._start_at_login)

        maintenance = QGroupBox(_("Maintenance"))
        ml = QVBoxLayout(maintenance)
        self._btn_clear_icon_cache = QPushButton(_("Reset all application data…"))
        self._btn_clear_icon_cache.setMaximumWidth(240)
        self._btn_clear_icon_cache.clicked.connect(self._on_clear_icon_cache_clicked)
        ml.addWidget(self._btn_clear_icon_cache)

        outer.addWidget(theme_box)
        outer.addWidget(icon_pack_box)
        outer.addWidget(session)
        outer.addWidget(maintenance)
        outer.addStretch(1)
        return page

    def _build_notifications_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        group = QGroupBox(_("Desktop notifications"))
        gl = QVBoxLayout(group)
        self._notif_group    = QButtonGroup(self)
        self._notif_off      = QRadioButton(_("Notifications off"))
        self._notif_monitored = QRadioButton(_("Monitored devices only"))
        self._notif_all      = QRadioButton(_("All devices"))
        self._notif_group.addButton(self._notif_off,       0)
        self._notif_group.addButton(self._notif_monitored, 1)
        self._notif_group.addButton(self._notif_all,       2)
        gl.addWidget(self._notif_off)
        gl.addWidget(self._notif_monitored)
        gl.addWidget(self._notif_all)

        note = QLabel(
            _(
                "Notification delivery depends on the OS and session. "
                "Tray-backed behaviour (close to tray, minimized start) will align "
                "once the system tray is implemented."
            )
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")

        layout.addWidget(group)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _build_locations_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        intro = QLabel(_("Manage location presets shown in the right-click menu."))
        intro.setWordWrap(True)

        self._loc_auto_add = QCheckBox(
            _("Automatically add locations discovered on the network to this list")
        )

        self._loc_list = QListWidget()
        self._loc_list.setStyleSheet(_LIST_QSS)

        btn_add              = QPushButton(_("Add"))
        self._loc_btn_rename = QPushButton(_("Rename"))
        self._loc_btn_remove = QPushButton(_("Remove"))
        btn_clear            = QPushButton(_("Clear all"))
        btn_add.clicked.connect(self._loc_on_add)
        self._loc_btn_rename.clicked.connect(self._loc_on_rename)
        self._loc_btn_remove.clicked.connect(self._loc_on_remove)
        btn_clear.clicked.connect(self._loc_on_clear)
        self._loc_list.currentRowChanged.connect(self._loc_update_buttons)

        ctrl = QHBoxLayout()
        ctrl.addWidget(btn_add)
        ctrl.addWidget(self._loc_btn_rename)
        ctrl.addWidget(self._loc_btn_remove)
        ctrl.addWidget(btn_clear)
        ctrl.addStretch(1)

        layout.addWidget(intro)
        layout.addSpacing(4)
        layout.addWidget(self._loc_auto_add)
        layout.addWidget(self._loc_list, 1)
        layout.addLayout(ctrl)
        return page

    def _build_types_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        intro = QLabel(_("Manage device type presets shown in the right-click menu."))
        intro.setWordWrap(True)

        self._typ_tree = QTreeWidget()
        self._typ_tree.setColumnCount(2)
        self._typ_tree.setHeaderLabels([_("Label"), _("Type ID")])
        self._typ_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._typ_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._typ_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._typ_tree.setRootIsDecorated(False)
        self._typ_tree.setAllColumnsShowFocus(True)
        self._typ_tree.setStyleSheet(_TREE_QSS)
        self._typ_tree.currentItemChanged.connect(
            lambda cur, _prev: self._typ_update_buttons(cur is not None)
        )

        btn_add              = QPushButton(_("Add"))
        self._typ_btn_edit   = QPushButton(_("Edit"))
        self._typ_btn_remove = QPushButton(_("Remove"))
        btn_restore          = QPushButton(_("Restore defaults"))
        btn_add.clicked.connect(self._typ_on_add)
        self._typ_btn_edit.clicked.connect(self._typ_on_edit)
        self._typ_btn_remove.clicked.connect(self._typ_on_remove)
        btn_restore.clicked.connect(self._typ_on_restore)

        ctrl = QHBoxLayout()
        ctrl.addWidget(btn_add)
        ctrl.addWidget(self._typ_btn_edit)
        ctrl.addWidget(self._typ_btn_remove)
        ctrl.addStretch(1)
        ctrl.addWidget(btn_restore)

        layout.addWidget(intro)
        layout.addWidget(self._typ_tree, 1)
        layout.addLayout(ctrl)
        return page

    def _build_applications_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        intro = QLabel(
            _("Command templates for opening device connections. Leave empty to use the system default.")
        )
        intro.setWordWrap(True)

        defaults = default_connect_command_templates()
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self._app_entries: dict[str, QLineEdit] = {}
        for i, (scheme, title) in enumerate(_SCHEME_ROWS):
            lbl = QLabel(title + ":")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            ent = QLineEdit()
            ent.setPlaceholderText(_("Empty = system default"))
            reset_btn = QPushButton(_("Reset"))
            reset_btn.setFixedWidth(64)
            default_val = defaults.get(scheme, "")
            reset_btn.clicked.connect(
                lambda _checked=False, e=ent, v=default_val: e.setText(v)
            )
            grid.addWidget(lbl,       i, 0)
            grid.addWidget(ent,       i, 1)
            grid.addWidget(reset_btn, i, 2)
            self._app_entries[scheme] = ent

        custom_row = len(_SCHEME_ROWS)
        custom_lbl = QLabel(_("Custom:"))
        custom_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._app_custom_edit = QLineEdit()
        self._app_custom_edit.setPlaceholderText(_("Empty = disabled"))
        clear_btn = QPushButton(_("Clear"))
        clear_btn.setFixedWidth(64)
        clear_btn.clicked.connect(lambda: self._app_custom_edit.clear())
        grid.addWidget(custom_lbl,          custom_row, 0)
        grid.addWidget(self._app_custom_edit, custom_row, 1)
        grid.addWidget(clear_btn,           custom_row, 2)

        hint = QLabel(
            "  ".join([
                "<b>{url}</b> " + _("full address"),
                "<b>{ip}</b> " + _("device IP"),
                "<b>{port}</b> " + _("service port"),
                "<b>{name}</b> " + _("device name"),
                "<b>{type}</b> " + _("device type"),
            ])
        )
        hint.setTextFormat(Qt.TextFormat.RichText)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: smaller;")

        layout.addWidget(intro)
        layout.addSpacing(4)
        layout.addLayout(grid)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    def _on_clear_icon_cache_clicked(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(_("Reset all application data?"))
        box.setText(
            _(
                "All preferences, cached icons, discovery data and logs will be "
                "permanently deleted:\n"
                "• ~/.config/netneighbor\n"
                "• ~/.cache/netneighbor\n\n"
                "NetNeighbor will use default settings on next start.\n"
                "This action cannot be undone."
            )
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Ok:
            return
        if self._on_clear_icon_cache is not None:
            self._on_clear_icon_cache()
        self.reject()
        if self._on_restart is not None:
            self._on_restart()

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def _load_widgets(self) -> None:
        # Theme
        theme_pref = str(self._prefs.get("theme", "auto"))
        if theme_pref == "light":
            self._btn_theme_light.setChecked(True)
        elif theme_pref == "dark":
            self._btn_theme_dark.setChecked(True)
        else:
            self._btn_theme_auto.setChecked(True)

        # Icon pack
        from utils.icon_packs import load_available_packs, BUILTIN_PACK_ID
        self._icon_packs = load_available_packs()
        current_pack_id = str(self._prefs.get("icon_pack", BUILTIN_PACK_ID))
        self._icon_pack_combo.blockSignals(True)
        for pack in self._icon_packs:
            self._icon_pack_combo.addItem(pack.name, pack.pack_id)
        select_idx = 0
        for i in range(self._icon_pack_combo.count()):
            if self._icon_pack_combo.itemData(i) == current_pack_id:
                select_idx = i
                break
        self._icon_pack_combo.setCurrentIndex(select_idx)
        self._icon_pack_combo.blockSignals(False)
        self._update_icon_pack_preview(self._icon_pack_combo.itemData(select_idx) or BUILTIN_PACK_ID)

        # Session
        self._close_to_tray.setChecked(bool(self._prefs.get("close_to_tray", True)))
        self._start_minimized.setChecked(bool(self._prefs.get("start_minimized_to_tray", False)))
        self._start_at_login.setChecked(
            bool(self._prefs.get("start_at_login", autostart_enabled_on_disk()))
        )

        # Notifications
        mode = str(self._prefs.get("notification_mode", "off"))
        if mode == "monitored":
            self._notif_monitored.setChecked(True)
        elif mode == "all":
            self._notif_all.setChecked(True)
        else:
            self._notif_off.setChecked(True)

        # Locations
        raw_locs = self._prefs.get("location_options")
        initial_locs = normalize_location_options(
            [str(v).strip() for v in raw_locs if isinstance(v, str) and str(v).strip()]
        ) if isinstance(raw_locs, list) else []
        for opt in (initial_locs or _default_location_presets()):
            self._loc_list.addItem(opt)
        self._loc_auto_add.setChecked(bool(self._prefs.get("auto_add_discovered_locations", False)))
        self._loc_list.clearSelection()
        self._loc_update_buttons(-1)

        # Types
        raw_types = self._prefs.get("type_options")
        initial_types: list[tuple[str, str]] = []
        if isinstance(raw_types, list):
            for entry in raw_types:
                if isinstance(entry, dict):
                    lbl = entry.get("label")
                    slg = entry.get("slug")
                    if isinstance(lbl, str) and isinstance(slg, str) and slg.strip():
                        initial_types.append((lbl, slg.strip().lower()))
        for label, slug in (initial_types or _default_type_options()):
            QTreeWidgetItem(self._typ_tree, [label, slug])
        self._typ_tree.clearSelection()
        self._typ_tree.setCurrentItem(None)
        self._typ_update_buttons(False)

        # Applications
        templates = normalize_connect_templates(self._prefs.get("connect_command_templates"))
        custom    = str(self._prefs.get("custom_command_template", "") or "")
        for scheme, ent in self._app_entries.items():
            ent.setText(templates.get(scheme, ""))
        self._app_custom_edit.setText(custom)

    def _accept_save(self) -> None:
        prefs = dict(self._prefs)

        # Theme
        tid = self._theme_group.checkedId()
        theme_val = "light" if tid == 0 else "dark" if tid == 2 else "auto"
        prefs["theme"] = theme_val

        # Icon pack
        pack_idx = self._icon_pack_combo.currentIndex()
        new_pack_id = self._icon_pack_combo.itemData(pack_idx) if pack_idx >= 0 else ""
        from utils.icon_packs import BUILTIN_PACK_ID
        if not new_pack_id:
            new_pack_id = BUILTIN_PACK_ID
        old_pack_id = str(prefs.get("icon_pack", BUILTIN_PACK_ID))
        prefs["icon_pack"] = new_pack_id

        # Session
        prefs["close_to_tray"]            = self._close_to_tray.isChecked()
        prefs["start_minimized_to_tray"]  = self._start_minimized.isChecked()
        prefs["start_at_login"]           = self._start_at_login.isChecked()

        # Notifications
        if self._notif_monitored.isChecked():
            prefs["notification_mode"] = "monitored"
        elif self._notif_all.isChecked():
            prefs["notification_mode"] = "all"
        else:
            prefs["notification_mode"] = "off"

        # Locations
        loc_options = normalize_location_options(
            [self._loc_list.item(i).text() for i in range(self._loc_list.count())]
        )
        auto_add = self._loc_auto_add.isChecked()
        prefs["location_options"]              = loc_options
        prefs["auto_add_discovered_locations"] = auto_add

        # Types
        type_options: list[tuple[str, str]] = []
        for i in range(self._typ_tree.topLevelItemCount()):
            item = self._typ_tree.topLevelItem(i)
            lbl  = item.text(0).strip()
            slg  = item.text(1).strip().lower()
            if lbl and slg:
                type_options.append((lbl, slg))
        prefs["type_options"] = [{"label": lbl, "slug": slg} for lbl, slg in type_options]

        # Applications
        templates = {scheme: ent.text().strip() for scheme, ent in self._app_entries.items()}
        custom    = self._app_custom_edit.text().strip()
        prefs["connect_command_templates"] = templates
        prefs["custom_command_template"]   = custom

        save_ui_preferences(prefs)
        apply_autostart_pref(bool(prefs.get("start_at_login")))
        self._apply_theme(theme_val)

        if self._on_location_presets_saved:
            self._on_location_presets_saved(loc_options, auto_add)
        if self._on_type_presets_saved:
            self._on_type_presets_saved(type_options)
        if self._on_connect_templates_saved:
            self._on_connect_templates_saved(templates, custom)
        if self._on_icon_pack_changed and new_pack_id != old_pack_id:
            self._on_icon_pack_changed(new_pack_id)

        self.accept()

    # ------------------------------------------------------------------
    # Icon pack tab actions
    # ------------------------------------------------------------------

    def _on_icon_pack_combo_changed(self, index: int) -> None:
        pack_id = self._icon_pack_combo.itemData(index)
        if pack_id:
            self._update_icon_pack_preview(pack_id)

    def _update_icon_pack_preview(self, pack_id: str) -> None:
        # Clear previous strip
        while self._icon_pack_strip_layout.count():
            item = self._icon_pack_strip_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        pack = next((p for p in self._icon_packs if p.pack_id == pack_id), None)
        if pack is None:
            return

        from utils.icon_packs import find_icon_in_pack, scan_pack_sizes
        ICON_PX = 48
        sizes = scan_pack_sizes(pack.root)
        if not sizes:
            # Built-in: use ui.icons sizes
            from ui.icons import bundled_freedesktop_png_side_sizes
            sizes = bundled_freedesktop_png_side_sizes()
        if not sizes:
            return
        best_sz = min(sizes, key=lambda s: (abs(s - ICON_PX), s))

        # Collect all icon files at best_sz
        icon_files: list[str] = []
        for dir_name in (f"{best_sz}x{best_sz}", str(best_sz)):
            size_dir = pack.root / dir_name
            if size_dir.is_dir():
                icon_files = sorted(str(p) for p in size_dir.glob("*.png"))
                break

        for icon_path in icon_files:
            pix = QPixmap(icon_path)
            if pix.isNull():
                continue
            pix = pix.scaled(
                ICON_PX, ICON_PX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            lbl = QLabel()
            lbl.setPixmap(pix)
            lbl.setFixedSize(ICON_PX + 2, ICON_PX + 2)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._icon_pack_strip_layout.addWidget(lbl)
        self._icon_pack_strip_layout.addStretch(1)

    def _on_open_icon_packs_folder(self) -> None:
        from utils.icon_packs import user_icon_packs_dir
        import subprocess
        folder = user_icon_packs_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(folder))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        self._icon_folder_opened = True

    def changeEvent(self, event: object) -> None:
        from PySide6.QtCore import QEvent
        super().changeEvent(event)  # type: ignore[arg-type]
        if (
            isinstance(event, QEvent)
            and event.type() == QEvent.Type.ActivationChange
            and self.isActiveWindow()
            and self._icon_folder_opened
        ):
            self._icon_folder_opened = False
            self._refresh_icon_pack_list()

    def _refresh_icon_pack_list(self) -> None:
        from utils.icon_packs import load_available_packs, BUILTIN_PACK_ID
        current_id = self._icon_pack_combo.currentData() or BUILTIN_PACK_ID
        self._icon_packs = load_available_packs()
        self._icon_pack_combo.blockSignals(True)
        self._icon_pack_combo.clear()
        for pack in self._icon_packs:
            self._icon_pack_combo.addItem(pack.name, pack.pack_id)
        select_idx = 0
        for i in range(self._icon_pack_combo.count()):
            if self._icon_pack_combo.itemData(i) == current_id:
                select_idx = i
                break
        self._icon_pack_combo.setCurrentIndex(select_idx)
        self._icon_pack_combo.blockSignals(False)
        self._update_icon_pack_preview(self._icon_pack_combo.itemData(select_idx) or BUILTIN_PACK_ID)

    def _on_theme_toggled(self, button_id: int, checked: bool) -> None:
        if not checked:
            return
        theme = "light" if button_id == 0 else "dark" if button_id == 2 else "auto"
        self._apply_theme(theme)

    def _cancel(self) -> None:
        self._apply_theme(self._original_theme)
        self.reject()

    def _apply_theme(self, theme: str) -> None:
        from PySide6.QtWidgets import QApplication
        from ui.app_theme import apply_color_scheme
        app = QApplication.instance()
        if app is None:
            return
        if theme == "light":
            apply_color_scheme(app, Qt.ColorScheme.Light)
        elif theme == "dark":
            apply_color_scheme(app, Qt.ColorScheme.Dark)
        else:
            apply_color_scheme(app, None)

    # ------------------------------------------------------------------
    # Locations tab actions
    # ------------------------------------------------------------------

    def _loc_update_buttons(self, row: int) -> None:
        has = row >= 0
        self._loc_btn_rename.setEnabled(has)
        self._loc_btn_remove.setEnabled(has)

    def _loc_on_add(self) -> None:
        text, ok = QInputDialog.getText(self, _("Add location preset"), _("Location name:"))
        text = text.strip()
        existing = [self._loc_list.item(i).text() for i in range(self._loc_list.count())]
        if not ok or not text or text in existing:
            return
        self._loc_list.addItem(text)

    def _loc_on_rename(self) -> None:
        item = self._loc_list.currentItem()
        if item is None:
            return
        old = item.text()
        text, ok = QInputDialog.getText(self, _("Rename location preset"), _("New name:"), text=old)
        text = text.strip()
        existing = [self._loc_list.item(i).text() for i in range(self._loc_list.count())]
        if not ok or not text or text == old or text in existing:
            return
        item.setText(text)

    def _loc_on_remove(self) -> None:
        row = self._loc_list.currentRow()
        if row >= 0:
            self._loc_list.takeItem(row)
            self._loc_update_buttons(self._loc_list.currentRow())

    def _loc_on_clear(self) -> None:
        self._loc_list.clear()
        self._loc_update_buttons(-1)

    # ------------------------------------------------------------------
    # Types tab actions
    # ------------------------------------------------------------------

    def _typ_update_buttons(self, has: bool) -> None:
        self._typ_btn_edit.setEnabled(has)
        self._typ_btn_remove.setEnabled(has)

    def _typ_on_add(self) -> None:
        dlg = _TypeEntryDialog(self, title=_("Add type preset"))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        label, slug = dlg.result_entry()
        if label and slug:
            QTreeWidgetItem(self._typ_tree, [label, slug])

    def _typ_on_edit(self) -> None:
        item = self._typ_tree.currentItem()
        if item is None:
            return
        dlg = _TypeEntryDialog(self, title=_("Edit type preset"), label=item.text(0), slug=item.text(1))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        label, slug = dlg.result_entry()
        if label and slug:
            item.setText(0, label)
            item.setText(1, slug)

    def _typ_on_remove(self) -> None:
        item = self._typ_tree.currentItem()
        if item is not None:
            self._typ_tree.takeTopLevelItem(self._typ_tree.indexOfTopLevelItem(item))

    def _typ_on_restore(self) -> None:
        self._typ_tree.clear()
        for label, slug in _default_type_options():
            QTreeWidgetItem(self._typ_tree, [label, slug])
        self._typ_tree.clearSelection()
        self._typ_tree.setCurrentItem(None)
        self._typ_update_buttons(False)
