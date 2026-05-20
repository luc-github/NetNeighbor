# File qt_device_icons.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Per-device-type icons: active icon pack + ``assets/icons/netneighbor`` built-in fallback.

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

from utils.icon_view_prefs import (
    DEVICE_ICON_REFERENCE_PX,
    ICON_SIZE_PRESET_PIXELS,
    normalize_icon_size_preset,
)
from utils.type_icon_config import type_icon_basenames_for_slug


def _bundled_freedesktop_qicon(
    basename: str, *, preferred_px: int = DEVICE_ICON_REFERENCE_PX
) -> QIcon | None:
    """Build a multi-resolution ``QIcon`` — active icon pack first, built-in fallback.

    Supports both ``{N}x{N}/`` and flat ``{N}/`` directory naming in user packs.
    """
    from ui.icons import bundled_freedesktop_png_side_sizes, _bundled_freedesktop_root
    from utils.icon_packs import (
        BUILTIN_PACK_ID,
        active_icon_pack_id,
        active_icon_pack_root,
        find_icon_in_pack,
        scan_pack_sizes,
    )

    raw = str(basename).strip()
    stem = Path(raw).name
    if not stem or stem != raw:
        return None

    builtin_root = _bundled_freedesktop_root()
    pack_id = active_icon_pack_id()
    pack_root = active_icon_pack_root() if pack_id != BUILTIN_PACK_ID else None

    builtin_sizes = bundled_freedesktop_png_side_sizes()
    icon = QIcon()
    registered: set[int] = set()

    # Sizes present in built-in: prefer pack version, fall back to built-in.
    for sz in builtin_sizes:
        p = find_icon_in_pack(pack_root, sz, stem) if pack_root is not None else None
        if p is None:
            p = builtin_root / f"{sz}x{sz}" / f"{stem}.png"
            if not p.is_file():
                continue
        icon.addFile(str(p), QSize(sz, sz))
        registered.add(sz)

    # Extra sizes only the active pack provides (e.g. 22, 128 not in built-in).
    if pack_root is not None:
        for sz in scan_pack_sizes(pack_root):
            if sz in registered:
                continue
            p = find_icon_in_pack(pack_root, sz, stem)
            if p is not None:
                icon.addFile(str(p), QSize(sz, sz))
                registered.add(sz)

    if preferred_px not in registered and registered:
        best = min(registered, key=lambda s: (abs(s - preferred_px), s))
        p = find_icon_in_pack(pack_root, best, stem) if pack_root is not None else None
        if p is None:
            p = builtin_root / f"{best}x{best}" / f"{stem}.png"
            p = p if p.is_file() else None
        if p is not None:
            icon.addFile(str(p), QSize(preferred_px, preferred_px))

    for root in ([pack_root] if pack_root is not None else []) + [builtin_root]:
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
    _ = style, widget  # API stability with existing callers.
    preset = normalize_icon_size_preset(icon_size_preset)
    preferred_px = ICON_SIZE_PRESET_PIXELS.get(preset, DEVICE_ICON_REFERENCE_PX)

    from ui.icons import resolve_asset_icon_file

    for icon_name in type_icon_basenames_for_slug(device_type):
        ic = _bundled_freedesktop_qicon(icon_name, preferred_px=preferred_px)
        if ic is not None and not ic.isNull():
            return ic

    unk = resolve_asset_icon_file("unknown.png")
    if unk is not None:
        return QIcon(str(unk))
    return QIcon()
