# File qt_device_icons.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Per-device-type icons: only ``assets/icons/bundled-freedesktop`` (see ``config/icons.json``).

No system theme, no Windows Shell stock, no ``QStyle`` fallbacks — assets only, then
``assets/icons/unknown.png`` if present.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon

if TYPE_CHECKING:
    from PySide6.QtWidgets import QStyle, QWidget

from utils.type_icon_config import type_icon_basenames_for_slug


def _bundled_freedesktop_qicon(basename: str) -> QIcon | None:
    """Build a multi-resolution ``QIcon`` from ``bundled-freedesktop`` (PNG folders + optional flat/SVG)."""
    from ui.icons import bundled_freedesktop_png_side_sizes

    raw = str(basename).strip()
    stem = Path(raw).name
    if not stem or stem != raw:
        return None
    root = Path(__file__).resolve().parent.parent / "assets" / "icons" / "bundled-freedesktop"
    icon = QIcon()
    for sz in bundled_freedesktop_png_side_sizes():
        p = root / f"{sz}x{sz}" / f"{stem}.png"
        if p.is_file():
            icon.addFile(str(p), QSize(sz, sz))
    for ext in (".svg", ".png"):
        flat = root / f"{stem}{ext}"
        if flat.is_file():
            icon.addFile(str(flat))
    return None if icon.isNull() else icon


def qt_icon_for_device_type(
    device_type: str | None,
    style: "QStyle",
    widget: "QWidget | None" = None,
    *,
    icon_size_preset: str = "medium",
) -> QIcon:
    """Icon for a logical device type: only bundled assets (``config/icons.json`` order)."""
    _ = icon_size_preset, style, widget  # API stability with existing callers.

    from ui.icons import resolve_asset_icon_file

    for icon_name in type_icon_basenames_for_slug(device_type):
        ic = _bundled_freedesktop_qicon(icon_name)
        if ic is not None and not ic.isNull():
            return ic

    unk = resolve_asset_icon_file("unknown.png")
    if unk is not None:
        return QIcon(str(unk))
    return QIcon()
