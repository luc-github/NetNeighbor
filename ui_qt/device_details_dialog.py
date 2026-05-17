# File device_details_dialog.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Device details dialog (Qt port of ``ui/device_details.py`` — core tabs)."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from gettext import gettext as _
import os
from typing import Any

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ui.icons import user_custom_icons_dir
from utils.details_payload import detail_field_value_visible
from utils.device_details_view import DeviceDetailsViewModel


@dataclass(slots=True)
class DeviceIconSettings:
    icon_mode: str
    has_device_icon_source: bool
    provided_icon_display: str | None
    selected_custom_icon_id: str | None
    on_apply: Callable[[str, str | None], None]
    on_pick_custom: Callable[[], str | None]


class DeviceDetailsDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        model: DeviceDetailsViewModel,
        *,
        initial_tab: str | None = None,
        icon_settings: DeviceIconSettings | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Device details") + f" - {model.title}")
        self.setModal(True)
        self.resize(720, 520)

        self._icon_settings = icon_settings
        self._icon_mode_group: QButtonGroup | None = None
        self._icon_detail_label: QLabel | None = None
        self._suppress_icon_signals = False
        self._last_icon_mode = "provided"

        tabs = QTabWidget()
        tabs.addTab(self._build_overview_tab(model), _("Overview"))
        services = self._build_services_tab(model)
        if services is not None:
            tabs.addTab(services, _("Services"))
        raw = self._build_raw_tab(model)
        if raw is not None:
            tabs.addTab(raw, _("Device data"))
        if icon_settings is not None:
            tabs.addTab(self._build_options_tab(icon_settings, model), _("Options"))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText(_("Close"))

        layout = QVBoxLayout(self)
        layout.addWidget(tabs, stretch=1)
        layout.addWidget(buttons)

        if initial_tab == "options":
            for i in range(tabs.count()):
                if tabs.tabText(i) == _("Options"):
                    tabs.setCurrentIndex(i)
                    break

    def _build_overview_tab(self, model: DeviceDetailsViewModel) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inner.setStyleSheet("background-color: palette(base);")
        grid = QGridLayout(inner)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)

        row = 0
        ov = model.overview
        for label_key, key in (
            (_("Name"), "name"),
            (_("IP address"), "ip"),
            (_("Location"), "location"),
            (_("Type"), "type"),
        ):
            val = (ov.get(key) or "").strip() if isinstance(ov.get(key), str) else ""
            if not detail_field_value_visible(val):
                continue
            row = self._add_overview_row(grid, row, label_key, val)

        ip6 = (ov.get("ipv6_link_local") or "").strip()
        if detail_field_value_visible(ip6):
            row = self._add_overview_row(grid, row, _("IPv6 (link-local)"), ip6)

        for key, value in model.fields:
            if not detail_field_value_visible(value):
                continue
            row = self._add_overview_row(grid, row, str(key), value)

        grid.setRowStretch(row, 1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        return page

    @staticmethod
    def _add_overview_row(grid: QGridLayout, row: int, label: str, value: str) -> int:
        key_lbl = QLabel(f"{label}:")
        key_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop
        )
        key_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        val_lbl = QLabel(value)
        val_lbl.setWordWrap(True)
        val_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        val_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        val_lbl.setMinimumWidth(420)
        grid.addWidget(key_lbl, row, 0)
        grid.addWidget(val_lbl, row, 1)
        return row + 1

    def _build_services_tab(self, model: DeviceDetailsViewModel) -> QWidget | None:
        if model.mdns_service_sections:
            return self._build_mdns_services_page(model.mdns_service_sections)
        if not model.services_records:
            return None
        table = QTableWidget(len(model.services_records), 3)
        table.setHorizontalHeaderLabels([_("Service"), _("Target"), _("Port")])
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        for r, (svc, target, port) in enumerate(model.services_records):
            table.setItem(r, 0, QTableWidgetItem(svc))
            table.setItem(r, 1, QTableWidgetItem(target))
            table.setItem(r, 2, QTableWidgetItem(port))
        table.resizeColumnsToContents()
        return table

    def _build_mdns_services_page(self, sections: list[dict[str, Any]]) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        for raw in sections:
            if isinstance(raw, dict):
                layout.addWidget(MdnsServiceSection(raw, expanded=False))
        layout.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def _build_raw_tab(self, model: DeviceDetailsViewModel) -> QWidget | None:
        raw = model.raw_content
        if not isinstance(raw, str) or not raw.strip():
            return None
        page = QWidget()
        layout = QVBoxLayout(page)
        if model.raw_xml_location:
            loc = QLabel(_("XML location") + ": " + model.raw_xml_location)
            loc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            loc.setWordWrap(True)
            layout.addWidget(loc)
        copy_btn = QPushButton(_("Copy to clipboard"))
        copy_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(raw))
        layout.addWidget(copy_btn)
        browser = QTextBrowser()
        browser.setPlainText(raw)
        browser.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        layout.addWidget(browser, stretch=1)
        return page

    def _build_options_tab(
        self, icon: DeviceIconSettings, model: DeviceDetailsViewModel
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        icon_box = QGroupBox(_("Use icon from"))
        icon_layout = QVBoxLayout(icon_box)
        self._icon_mode_group = QButtonGroup(self)

        rb_system = QRadioButton(_("System"))
        rb_provided = QRadioButton(_("From device"))
        rb_custom = QRadioButton(_("Custom"))
        self._icon_mode_group.addButton(rb_system, 0)
        self._icon_mode_group.addButton(rb_provided, 1)
        self._icon_mode_group.addButton(rb_custom, 2)
        icon_layout.addWidget(rb_system)
        if icon.has_device_icon_source:
            icon_layout.addWidget(rb_provided)
        icon_layout.addWidget(rb_custom)

        self._icon_detail_label = QLabel()
        self._icon_detail_label.setWordWrap(True)
        self._icon_detail_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        icon_layout.addWidget(self._icon_detail_label)

        open_folder_btn = QPushButton(_("Open custom icons folder"))
        open_folder_btn.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        open_folder_btn.clicked.connect(self._on_open_custom_icons_folder)
        icon_layout.addWidget(open_folder_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        mode = icon.icon_mode if icon.icon_mode in {"system", "provided", "custom"} else "provided"
        if mode == "provided" and not icon.has_device_icon_source:
            mode = "system"
        self._last_icon_mode = mode

        self._suppress_icon_signals = True
        if mode == "system":
            rb_system.setChecked(True)
        elif mode == "custom":
            rb_custom.setChecked(True)
        else:
            rb_provided.setChecked(True)
        self._suppress_icon_signals = False
        self._refresh_icon_detail_line(icon)

        self._icon_mode_group.idClicked.connect(
            lambda btn_id: self._on_icon_mode_clicked(btn_id, icon)
        )
        layout.addWidget(icon_box)

        if model.custom_command:
            cmd = QLabel(_("Custom command") + ": " + model.custom_command)
            cmd.setWordWrap(True)
            cmd.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(cmd)
        if model.url_override:
            url = QLabel(_("URL override") + ": " + model.url_override)
            url.setWordWrap(True)
            url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(url)

        layout.addStretch(1)
        return page

    def _current_icon_mode(self, icon: DeviceIconSettings) -> str:
        if self._icon_mode_group is None:
            return "system"
        btn_id = self._icon_mode_group.checkedId()
        if btn_id == 0:
            return "system"
        if btn_id == 2:
            return "custom"
        if icon.has_device_icon_source and btn_id == 1:
            return "provided"
        return "system"

    def _refresh_icon_detail_line(self, icon: DeviceIconSettings) -> None:
        if self._icon_detail_label is None:
            return
        mode = self._current_icon_mode(icon)
        if mode == "system":
            self._icon_detail_label.hide()
            return
        self._icon_detail_label.show()
        if mode == "provided":
            detail = icon.provided_icon_display or _("unavailable")
            self._icon_detail_label.setText(_("Image from device") + ": " + detail)
        else:
            label = icon.selected_custom_icon_id or _("none")
            self._icon_detail_label.setText(_("Selected icon") + ": " + label)

    def _restore_icon_mode_radio(self, icon: DeviceIconSettings) -> None:
        if self._icon_mode_group is None:
            return
        self._suppress_icon_signals = True
        target_id = 0
        if self._last_icon_mode == "custom":
            target_id = 2
        elif self._last_icon_mode == "provided" and icon.has_device_icon_source:
            target_id = 1
        btn = self._icon_mode_group.button(target_id)
        if btn is not None:
            btn.setChecked(True)
        self._suppress_icon_signals = False

    def _on_icon_mode_clicked(self, btn_id: int, icon: DeviceIconSettings) -> None:
        if self._suppress_icon_signals:
            return
        if btn_id == 2:
            picked = icon.on_pick_custom()
            if not picked:
                self._restore_icon_mode_radio(icon)
                return
            icon.on_apply("custom", picked)
            icon.selected_custom_icon_id = picked
            self._last_icon_mode = "custom"
            self._refresh_icon_detail_line(icon)
            return
        mode = "system" if btn_id == 0 else "provided"
        if mode == "provided" and not icon.has_device_icon_source:
            self._restore_icon_mode_radio(icon)
            return
        icon.on_apply(mode, None)
        self._last_icon_mode = mode
        self._refresh_icon_detail_line(icon)

    def _on_open_custom_icons_folder(self) -> None:
        folder = user_custom_icons_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(folder))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def _filter_mdns_txt_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    cleaned: list[tuple[str, str]] = []
    for key, value in pairs:
        key_str = str(key).strip()
        vs = value.strip().lower() if isinstance(value, str) else str(value).strip().lower()
        if vs == "unavailable" or not key_str:
            continue
        cleaned.append((key_str, value if isinstance(value, str) else str(value)))
    return cleaned


class MdnsServiceSection(QFrame):
    """One mDNS service block with a collapsible TXT / metadata body (GTK parity)."""

    def __init__(self, section: dict[str, Any], *, expanded: bool = False) -> None:
        super().__init__()
        self.setObjectName("nnMdnsServiceSection")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        heading = section.get("heading") or section.get("service_type") or _("Service")
        title = str(heading)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._title = title
        self._header = QPushButton()
        self._header.setObjectName("nnMdnsServiceHeader")
        self._header.setStyleSheet(
            "QPushButton#nnMdnsServiceHeader {"
            " background-color: palette(base);"
            " color: palette(text);"
            " border: none;"
            " border-bottom: 1px solid palette(mid);"
            " border-radius: 0px;"
            " padding: 6px 8px;"
            " font-weight: bold;"
            " text-align: left;"
            "}"
            "QPushButton#nnMdnsServiceHeader:hover {"
            " background-color: palette(alternate-base);"
            "}"
        )
        self._header.setCheckable(True)
        self._header.setChecked(expanded)
        self._header.setFlat(True)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._header.toggled.connect(self._on_header_toggled)
        self._update_header_text(expanded)
        outer.addWidget(self._header)

        self._body = QWidget()
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(12, 4, 8, 8)
        body_layout.setSpacing(8)

        meta = QGridLayout()
        meta.setColumnStretch(1, 1)
        meta.setHorizontalSpacing(10)
        meta.setVerticalSpacing(4)
        row = 0
        service_type = str(section.get("service_type", "")).strip()
        target = str(section.get("target", "")).strip()
        port = str(section.get("port", "")).strip()
        for label_key, val in (
            (_("Service type"), service_type),
            (_("Target"), target),
            (_("Port"), port or "—"),
        ):
            if label_key != _("Port") and not detail_field_value_visible(val):
                continue
            row = MdnsServiceSection._add_overview_row_to_grid(meta, row, label_key, val)

        body_layout.addLayout(meta)

        txt_lbl = QLabel(_("TXT records") + ":")
        txt_lbl.setStyleSheet("color: palette(mid);")
        body_layout.addWidget(txt_lbl)

        pairs: list[tuple[str, str]] = []
        pairs_raw = section.get("txt_records")
        if isinstance(pairs_raw, list):
            for item in pairs_raw:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    pairs.append((str(item[0]), "" if item[1] is None else str(item[1])))
        filtered = _filter_mdns_txt_pairs(pairs)
        if not filtered:
            none_lbl = QLabel(_("No TXT records"))
            none_lbl.setStyleSheet("color: palette(mid);")
            body_layout.addWidget(none_lbl)
        else:
            table = QTableWidget(len(filtered), 2)
            table.setHorizontalHeaderLabels([_("Name"), _("Value")])
            table.horizontalHeader().setStretchLastSection(True)
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            table.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            table.setStyleSheet(
                "QTableWidget { outline: none; }"
                "QTableWidget::item { border: none; }"
                "QTableWidget::item:focus { outline: none; border: none; }"
            )
            table.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            for r, (tk, tv) in enumerate(filtered):
                key_item = QTableWidgetItem(tk)
                val_item = QTableWidgetItem(tv)
                flags = Qt.ItemFlag.ItemIsEnabled
                key_item.setFlags(flags)
                val_item.setFlags(flags)
                table.setItem(r, 0, key_item)
                table.setItem(r, 1, val_item)
            table.resizeColumnsToContents()
            row_h = table.horizontalHeader().height() + len(filtered) * 26 + 8
            table.setFixedHeight(max(60, row_h))
            body_layout.addWidget(table)

        outer.addWidget(self._body)
        self._body.setVisible(expanded)

    def _update_header_text(self, expanded: bool) -> None:
        chevron = "▾" if expanded else "▸"
        self._header.setText(f"{chevron}  {self._title}")

    def _on_header_toggled(self, expanded: bool) -> None:
        self._update_header_text(expanded)
        self._body.setVisible(expanded)

    @staticmethod
    def _add_overview_row_to_grid(grid: QGridLayout, row: int, label: str, value: str) -> int:
        key_lbl = QLabel(f"{label}:")
        key_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        val_lbl = QLabel(value)
        val_lbl.setWordWrap(True)
        val_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        val_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        grid.addWidget(key_lbl, row, 0)
        grid.addWidget(val_lbl, row, 1)
        return row + 1


def show_device_details_dialog(
    parent: QWidget | None,
    model: DeviceDetailsViewModel,
    *,
    initial_tab: str | None = None,
    icon_settings: DeviceIconSettings | None = None,
) -> None:
    DeviceDetailsDialog(
        parent,
        model,
        initial_tab=initial_tab,
        icon_settings=icon_settings,
    ).exec()
