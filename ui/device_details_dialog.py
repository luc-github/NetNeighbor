# File device_details_dialog.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Device details dialog (Qt port of ``ui/device_details.py`` — core tabs)."""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from gettext import gettext as _
import os
from typing import Any

_LOG = logging.getLogger("ui.device_details")

from PySide6.QtCore import QPoint, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
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


_VALID_SCHEMES = ["http", "https", "smb", "ftp", "ssh", "sftp", "telnet", "custom"]
_VALID_MODES = ["override", "additional"]


@dataclass(slots=True)
class DeviceCommandSettings:
    device_commands: list[dict] = field(default_factory=list)
    on_set_device_commands: Callable[[list[dict]], None] = field(default=lambda _: None)


@dataclass(slots=True)
class DeviceIconSettings:
    icon_mode: str
    has_device_icon_source: bool
    provided_icon_display: str | None
    selected_custom_icon_id: str | None
    on_apply: Callable[[str, str | None], None]
    on_pick_custom: Callable[[], str | None]
    provided_icon_pixmap: QPixmap | None = None
    provided_icon_native_size: tuple[int, int] | None = None
    icon_cache: Any = None
    icon_devices: list = field(default_factory=list)


class DeviceDetailsDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        model: DeviceDetailsViewModel,
        *,
        initial_tab: str | None = None,
        icon_settings: DeviceIconSettings | None = None,
        command_settings: DeviceCommandSettings | None = None,
        on_set_field_rule: Callable[[str, str], None] | None = None,
        field_rules: dict[str, list[str]] | None = None,
        on_delete_field_rule: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Device details") + f" - {model.title}")
        self.setModal(True)
        self.resize(720, 520)

        self._icon_settings = icon_settings
        self._on_set_field_rule = on_set_field_rule
        self._field_rules: dict[str, list[str]] = field_rules or {}
        self._on_delete_field_rule = on_delete_field_rule
        self._field_rules_table: QTableWidget | None = None
        self._field_rules_rows: list[tuple[str, str]] = []
        self._field_rules_del_btn: QPushButton | None = None
        self._icon_mode_group: QButtonGroup | None = None
        self._icon_detail_label: QLabel | None = None
        self._provided_icon_preview: QLabel | None = None
        self._provided_icon_size_lbl: QLabel | None = None
        self._suppress_icon_signals = False
        self._last_icon_mode = "provided"
        self._cmd_table: QTableWidget | None = None
        self._live_icon_cache: Any = None
        self._live_icon_settings: DeviceIconSettings | None = None

        tabs = QTabWidget()
        tabs.addTab(self._build_overview_tab(model), _("Overview"))
        services = self._build_services_tab(model)
        if services is not None:
            tabs.addTab(services, _("Services"))
        raw = self._build_raw_tab(model)
        if raw is not None:
            tabs.addTab(raw, _("Device data"))
        if icon_settings is not None or command_settings is not None:
            tabs.addTab(
                self._build_options_tab(icon_settings, command_settings, model),
                _("Options"),
            )

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
        ip_field = (ov.get("ip") or "").strip()
        if detail_field_value_visible(ip6) and ip6 not in ip_field:
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
        show_rules = (
            self._on_set_field_rule is not None
            or self._on_delete_field_rule is not None
            or bool(self._field_rules)
        )
        rules_box: QWidget | None = None
        if show_rules:
            rules_box = self._build_field_rules_box(
                self._field_rules,
                self._on_delete_field_rule or (lambda t, f: None),
            )

        if model.mdns_service_sections:
            return self._build_mdns_services_page(model.mdns_service_sections, prefix_widget=rules_box)

        if not model.services_records:
            if rules_box is None:
                return None
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(12, 12, 12, 12)
            lay.addWidget(rules_box)
            lay.addStretch(1)
            return page

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
        if rules_box is None:
            return table
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        lay.addWidget(rules_box)
        lay.addWidget(table, stretch=1)
        return page

    def _build_mdns_services_page(
        self, sections: list[dict[str, Any]], *, prefix_widget: QWidget | None = None
    ) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inner.setStyleSheet("background-color: palette(base);")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        if prefix_widget is not None:
            layout.addWidget(prefix_widget)
        txt_rule_handler = self._handle_txt_rule if self._on_set_field_rule is not None else None
        for raw in sections:
            if isinstance(raw, dict):
                layout.addWidget(
                    MdnsServiceSection(
                        raw,
                        expanded=False,
                        on_txt_rule=txt_rule_handler,
                    )
                )
        layout.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def _build_field_rules_box(
        self,
        rules: dict[str, list[str]],
        on_delete: Callable[[str, str], None],
    ) -> QGroupBox:
        self._field_rules_rows.clear()
        for target_key, paths in rules.items():
            for path in paths:
                self._field_rules_rows.append((target_key, path))

        box = QGroupBox(_("Field mapping rules"))
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(8, 4, 8, 8)
        box_layout.setSpacing(4)

        table = QTableWidget(len(self._field_rules_rows), 2)
        self._field_rules_table = table
        table.setHorizontalHeaderLabels([_("Target"), _("Field")])
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        self._populate_field_rules_table()
        row_h = table.horizontalHeader().height() + len(self._field_rules_rows) * 26 + 8
        table.setFixedHeight(max(60, row_h))
        box_layout.addWidget(table)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        del_btn = QPushButton(_("Remove"))
        self._field_rules_del_btn = del_btn
        del_btn.setEnabled(False)
        btn_row.addWidget(del_btn)
        box_layout.addLayout(btn_row)

        def _on_selection() -> None:
            del_btn.setEnabled(table.currentRow() >= 0)

        def _on_remove() -> None:
            r = table.currentRow()
            if r < 0 or r >= len(self._field_rules_rows):
                return
            tk, fp = self._field_rules_rows[r]
            self._field_rules_rows.pop(r)
            # Keep _field_rules in sync
            bucket = self._field_rules.get(tk, [])
            if fp in bucket:
                bucket.remove(fp)
            if not bucket:
                self._field_rules.pop(tk, None)
            table.removeRow(r)
            new_h = table.horizontalHeader().height() + len(self._field_rules_rows) * 26 + 8
            table.setFixedHeight(max(60, new_h))
            del_btn.setEnabled(table.rowCount() > 0 and table.currentRow() >= 0)
            on_delete(tk, fp)

        table.itemSelectionChanged.connect(_on_selection)
        del_btn.clicked.connect(_on_remove)
        return box

    def _populate_field_rules_table(self) -> None:
        """Fill the rules table from self._field_rules_rows (no resize)."""
        if self._field_rules_table is None:
            return
        target_labels = {
            "name": _("Friendly name"),
            "location": _("Location"),
            "information": _("Information"),
        }
        table = self._field_rules_table
        for r, (tk, fp) in enumerate(self._field_rules_rows):
            tgt_item = QTableWidgetItem(target_labels.get(tk, tk))
            tgt_item.setData(Qt.ItemDataRole.UserRole, tk)
            fp_item = QTableWidgetItem(fp)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            tgt_item.setFlags(flags)
            fp_item.setFlags(flags)
            table.setItem(r, 0, tgt_item)
            table.setItem(r, 1, fp_item)
        table.resizeColumnsToContents()

    def _refresh_field_rules_table(self) -> None:
        """Rebuild rules rows from self._field_rules and repopulate the table."""
        if self._field_rules_table is None:
            return
        self._field_rules_rows.clear()
        for tk, paths in self._field_rules.items():
            for fp in paths:
                self._field_rules_rows.append((tk, fp))
        table = self._field_rules_table
        table.setRowCount(len(self._field_rules_rows))
        self._populate_field_rules_table()
        new_h = table.horizontalHeader().height() + len(self._field_rules_rows) * 26 + 8
        table.setFixedHeight(max(60, new_h))
        if self._field_rules_del_btn is not None:
            self._field_rules_del_btn.setEnabled(False)

    def _handle_txt_rule(self, target: str, field_path: str) -> None:
        """Intercepts TXT right-click rule assignment: updates local state then calls external cb."""
        # Replace semantics: one path per target
        self._field_rules[target] = [field_path]
        self._refresh_field_rules_table()
        if self._on_set_field_rule is not None:
            self._on_set_field_rule(target, field_path)

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
        self,
        icon: DeviceIconSettings | None,
        cmd: DeviceCommandSettings | None,
        model: DeviceDetailsViewModel,
    ) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inner.setStyleSheet("background-color: palette(base);")
        layout = QVBoxLayout(inner)

        if icon is not None:
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

            self._provided_icon_preview = QLabel()
            self._provided_icon_preview.setFixedSize(48, 48)
            self._provided_icon_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._provided_icon_preview.setStyleSheet("background: transparent;")
            self._provided_icon_size_lbl = QLabel()
            self._provided_icon_size_lbl.setStyleSheet("color: palette(mid);")
            preview_row = QHBoxLayout()
            preview_row.setContentsMargins(0, 0, 0, 0)
            preview_row.addWidget(self._provided_icon_preview)
            preview_row.addWidget(self._provided_icon_size_lbl)
            preview_row.addStretch(1)
            icon_layout.addLayout(preview_row)
            self._refresh_provided_icon_preview(icon)

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

            if icon.icon_cache is not None:
                self._live_icon_settings = icon
                self._live_icon_cache = icon.icon_cache
                _LOG.warning("dialog: triggering fetch for %d icon_devices", len(icon.icon_devices))
                for dev in icon.icon_devices:
                    _LOG.warning("dialog: trigger_fetch ip=%s source=%s", dev.ip, dev.source)
                    icon.icon_cache.trigger_fetch_for_device(dev)
                icon.icon_cache.icons_ready.connect(self._on_icon_cache_ready)
                self.finished.connect(self._disconnect_icon_cache)

        if cmd is not None:
            layout.addWidget(self._build_device_commands_box(cmd))

        layout.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        return page

    def _build_device_commands_box(self, cmd: DeviceCommandSettings) -> QGroupBox:
        box = QGroupBox(_("Commands"))
        bl = QVBoxLayout(box)

        hint = QLabel(
            _("Override or add connection endpoints per protocol. "
              "For 'custom' scheme, enter the command template in IP/Host "
              "({ip}, {ip_raw}, {port}, {name}, {type}, {category}, {url}). "
              "IP/Host and Port are optional for other schemes.")
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        bl.addWidget(hint)

        self._cmd_table = QTableWidget(0, 5)
        self._cmd_table.setHorizontalHeaderLabels(
            [_("Scheme"), _("Command"), _("Port"), _("Mode"), _("Label")]
        )
        hdr = self._cmd_table.horizontalHeader()
        hdr.setStretchLastSection(True)
        hdr.resizeSection(0, 90)
        hdr.resizeSection(2, 60)
        hdr.resizeSection(3, 100)
        self._cmd_table.verticalHeader().setVisible(False)
        self._cmd_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._cmd_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        for entry in (cmd.device_commands or []):
            self._append_command_row(entry)

        bl.addWidget(self._cmd_table)

        btns = QHBoxLayout()
        add_btn = QPushButton(_("Add"))
        add_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        add_btn.clicked.connect(self._on_cmd_add_row)
        del_btn = QPushButton(_("Delete"))
        del_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        del_btn.clicked.connect(self._on_cmd_delete_row)
        apply_btn = QPushButton(_("Apply"))
        apply_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        apply_btn.clicked.connect(lambda: self._on_cmd_table_apply(cmd))
        btns.addWidget(add_btn)
        btns.addWidget(del_btn)
        btns.addStretch(1)
        btns.addWidget(apply_btn)
        bl.addLayout(btns)
        return box

    def _append_command_row(self, entry: dict | None = None) -> None:
        if self._cmd_table is None:
            return
        r = self._cmd_table.rowCount()
        self._cmd_table.insertRow(r)

        scheme_combo = QComboBox()
        for s in _VALID_SCHEMES:
            scheme_combo.addItem(s)
        if entry:
            idx = scheme_combo.findText(str(entry.get("scheme", "http")))
            scheme_combo.setCurrentIndex(max(0, idx))
        self._cmd_table.setCellWidget(r, 0, scheme_combo)

        ip_item = QTableWidgetItem(str(entry.get("ip", "")) if entry else "")
        self._cmd_table.setItem(r, 1, ip_item)

        port_val = entry.get("port", 0) if entry else 0
        port_item = QTableWidgetItem(str(port_val) if port_val else "")
        self._cmd_table.setItem(r, 2, port_item)

        mode_combo = QComboBox()
        for m in _VALID_MODES:
            mode_combo.addItem(m)
        if entry:
            idx = mode_combo.findText(str(entry.get("mode", "override")))
            mode_combo.setCurrentIndex(max(0, idx))
        self._cmd_table.setCellWidget(r, 3, mode_combo)

        label_item = QTableWidgetItem(str(entry.get("label", "")) if entry else "")
        self._cmd_table.setItem(r, 4, label_item)

    def _on_cmd_add_row(self) -> None:
        self._append_command_row(None)

    def _on_cmd_delete_row(self) -> None:
        if self._cmd_table is None:
            return
        row = self._cmd_table.currentRow()
        if row >= 0:
            self._cmd_table.removeRow(row)

    def _read_cmd_table_rows(self) -> list[dict]:
        if self._cmd_table is None:
            return []
        result = []
        for r in range(self._cmd_table.rowCount()):
            scheme_w = self._cmd_table.cellWidget(r, 0)
            scheme = scheme_w.currentText() if isinstance(scheme_w, QComboBox) else "http"
            ip_item = self._cmd_table.item(r, 1)
            ip = ip_item.text().strip() if ip_item else ""
            port_item = self._cmd_table.item(r, 2)
            port_str = port_item.text().strip() if port_item else ""
            try:
                port = int(port_str) if port_str else 0
            except ValueError:
                port = 0
            mode_w = self._cmd_table.cellWidget(r, 3)
            mode = mode_w.currentText() if isinstance(mode_w, QComboBox) else "override"
            label_item = self._cmd_table.item(r, 4)
            label = label_item.text().strip() if label_item else ""
            result.append({"scheme": scheme, "ip": ip, "port": port, "mode": mode, "label": label})
        return result

    def _on_cmd_table_apply(self, cmd: DeviceCommandSettings) -> None:
        rows = self._read_cmd_table_rows()
        cmd.device_commands = rows
        cmd.on_set_device_commands(rows)

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

    def _refresh_provided_icon_preview(self, icon: DeviceIconSettings) -> None:
        if self._provided_icon_preview is None:
            return
        pix = icon.provided_icon_pixmap
        nat = icon.provided_icon_native_size
        has_source = icon.has_device_icon_source

        if not has_source:
            self._provided_icon_preview.hide()
            self._provided_icon_size_lbl.hide()
            return

        self._provided_icon_preview.show()
        self._provided_icon_size_lbl.show()

        if pix is not None and not pix.isNull():
            self._provided_icon_preview.setPixmap(
                pix.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            )
            if nat:
                self._provided_icon_size_lbl.setText(f"{nat[0]} × {nat[1]} px")
            else:
                self._provided_icon_size_lbl.setText(f"{pix.width()} × {pix.height()} px (cached)")
        else:
            self._provided_icon_preview.setText("?")
            if nat is None and icon.provided_icon_display:
                status = "missing"
                if icon.icon_cache is not None:
                    for dev in icon.icon_devices:
                        s = icon.icon_cache.fetch_status_for_device(dev)
                        if s != "missing":
                            status = s
                            break
                if status == "fetching":
                    self._provided_icon_size_lbl.setText(_("Downloading…"))
                elif status == "failed":
                    self._provided_icon_size_lbl.setText(_("Download failed"))
                else:
                    self._provided_icon_size_lbl.setText(_("not yet downloaded"))
            else:
                self._provided_icon_size_lbl.setText(_("unavailable"))

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

    def _on_icon_cache_ready(self) -> None:
        _LOG.warning("dialog: _on_icon_cache_ready fired")
        icon = self._live_icon_settings
        cache = self._live_icon_cache
        if icon is None or cache is None:
            _LOG.warning("dialog: _on_icon_cache_ready — no live settings/cache, ignoring")
            return
        found_bytes = False
        for dev in icon.icon_devices:
            raw = cache.bytes_for_device(dev)
            _LOG.warning("dialog: bytes_for_device ip=%s → %s bytes", dev.ip, len(raw) if raw else None)
            if raw:
                from ui.remote_icon_cache import pixmap_from_icon_bytes
                pix = pixmap_from_icon_bytes(raw, 128)
                if pix is not None and not pix.isNull():
                    icon.provided_icon_pixmap = pix
                    icon.provided_icon_native_size = (pix.width(), pix.height())
                    found_bytes = True
                    break
        _LOG.warning("dialog: _on_icon_cache_ready found_bytes=%s, refreshing preview", found_bytes)
        self._refresh_provided_icon_preview(icon)

    def _disconnect_icon_cache(self) -> None:
        cache = self._live_icon_cache
        if cache is not None:
            try:
                cache.icons_ready.disconnect(self._on_icon_cache_ready)
            except RuntimeError:
                pass
        self._live_icon_cache = None
        self._live_icon_settings = None


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

    def __init__(
        self,
        section: dict[str, Any],
        *,
        expanded: bool = False,
        on_txt_rule: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__()
        self._on_txt_rule = on_txt_rule
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
            table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            table.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            if self._on_txt_rule is not None:
                table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
                table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
                table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                table.customContextMenuRequested.connect(
                    lambda pos, t=table: self._show_txt_context_menu(pos, t)
                )
            else:
                table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
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
                flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
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

    def _show_txt_context_menu(self, pos: QPoint, table: QTableWidget) -> None:
        if self._on_txt_rule is None:
            return
        item = table.itemAt(pos)
        if item is None:
            return
        key_item = table.item(item.row(), 0)
        if key_item is None:
            return
        key = key_item.text()
        menu = QMenu(table)
        menu.addAction(
            _("Use as Friendly name"),
            lambda: self._on_txt_rule("name", f"txt:{key}"),  # type: ignore[misc]
        )
        menu.addAction(
            _("Use as Location"),
            lambda: self._on_txt_rule("location", f"txt:{key}"),  # type: ignore[misc]
        )
        menu.addAction(
            _("Use as Information"),
            lambda: self._on_txt_rule("information", f"txt:{key}"),  # type: ignore[misc]
        )
        menu.exec(table.viewport().mapToGlobal(pos))

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
    command_settings: DeviceCommandSettings | None = None,
    on_set_field_rule: Callable[[str, str], None] | None = None,
    field_rules: dict[str, list[str]] | None = None,
    on_delete_field_rule: Callable[[str, str], None] | None = None,
) -> None:
    DeviceDetailsDialog(
        parent,
        model,
        initial_tab=initial_tab,
        icon_settings=icon_settings,
        command_settings=command_settings,
        on_set_field_rule=on_set_field_rule,
        field_rules=field_rules,
        on_delete_field_rule=on_delete_field_rule,
    ).exec()
