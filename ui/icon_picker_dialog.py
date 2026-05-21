# File icon_picker_dialog.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Modal grid to pick a device icon (bundled pack + user PNGs); ids match the legacy details dialog."""

from __future__ import annotations

from gettext import gettext as _

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pathlib import Path

from ui.icons import iter_icon_picker_entries, user_custom_icons_dir


def pick_device_icon_id(
    parent: QWidget | None,
    *,
    preferred_type: str | None,
    current_id: str | None,
    active_pack_root: Path | None = None,
) -> str | None:
    """Run modal picker; returns ``bundled:…`` / ``custom:…`` id or ``None`` if cancelled."""
    dlg = IconPickerDialog(
        parent,
        preferred_type=preferred_type,
        current_id=current_id,
        active_pack_root=active_pack_root,
    )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.selected_icon_id()


class IconPickerDialog(QDialog):
    """Grid of pack + custom icons (same ids as the legacy device details dialog)."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        preferred_type: str | None,
        current_id: str | None,
        active_pack_root: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Choose icon"))
        self.resize(620, 484)
        self._selected_id: str | None = current_id.strip() if isinstance(current_id, str) and current_id.strip() else None
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        hint = QLabel(
            _("Select an icon, then OK. IDs are stored in your preferences.")
        )
        hint.setWordWrap(True)

        entries = iter_icon_picker_entries(preferred_type, active_pack_root)
        scroll: QScrollArea | None = None
        if not entries:
            empty = QLabel(
                _(
                    "No icons found. Add PNGs under assets/icons/netneighbor or ~/.config/netneighbor/custom_icons/."
                )
            )
            empty.setWordWrap(True)
        else:
            empty = None

        if entries:
            inner = QWidget()
            inner.setObjectName("nnIconPickerGrid")
            inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            inner.setStyleSheet(
                "#nnIconPickerGrid { background-color: palette(base); }"
                "QToolButton {"
                " background-color: palette(base);"
                " border: 1px solid palette(mid);"
                " border-radius: 4px;"
                "}"
                "QToolButton:hover {"
                " background-color: palette(alternate-base);"
                " border-color: palette(highlight);"
                "}"
                "QToolButton:checked {"
                " background-color: palette(highlight);"
                " border-color: palette(shadow);"
                "}"
                "QToolButton:checked:hover {"
                " background-color: palette(highlight);"
                " border-color: palette(shadow);"
                "}"
            )
            grid = QGridLayout(inner)
            grid.setContentsMargins(6, 6, 6, 6)
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(6)
            cols = 6
            row = col = 0
            first_btn: QToolButton | None = None
            matched = False
            for icon_id, label, path in entries:
                btn = QToolButton()
                btn.setToolTip(label)
                btn.setFixedSize(88, 88)
                btn.setIconSize(QSize(56, 56))
                if path is not None and path.is_file():
                    btn.setIcon(QIcon(str(path)))
                else:
                    btn.setText("?")
                btn.setCheckable(True)
                self._group.addButton(btn)

                def _on_toggled(checked: bool, iid: str = icon_id) -> None:
                    if checked:
                        self._selected_id = iid

                btn.toggled.connect(_on_toggled)
                grid.addWidget(btn, row, col)
                if first_btn is None:
                    first_btn = btn
                if self._selected_id and icon_id == self._selected_id:
                    btn.setChecked(True)
                    matched = True
                col += 1
                if col >= cols:
                    col = 0
                    row += 1

            if not matched:
                self._selected_id = entries[0][0]
                if first_btn is not None:
                    first_btn.setChecked(True)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(inner)

        folder_btn = QPushButton(_("Open custom icons folder"))
        folder_btn.clicked.connect(self._open_custom_folder)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        if not entries:
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

        row_btns = QHBoxLayout()
        row_btns.addWidget(folder_btn)
        row_btns.addStretch(1)

        outer = QVBoxLayout(self)
        outer.addWidget(hint)
        if empty is not None:
            outer.addWidget(empty)
        if scroll is not None:
            outer.addWidget(scroll, 1)
        outer.addLayout(row_btns)
        outer.addWidget(buttons)

    def selected_icon_id(self) -> str | None:
        return self._selected_id

    def _open_custom_folder(self) -> None:
        d = user_custom_icons_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d.resolve())))
