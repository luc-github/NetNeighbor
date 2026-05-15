# File icon_view_prefs.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Icon grid size presets (Explorer-like) for the Qt UI."""

from __future__ import annotations

from PySide6.QtCore import QSize

# Logical preset keys stored in ``ui_prefs.json`` under ``icon_size_preset``.
# Values are the **logical** side length (px at 100 % UI scaling) used for ``QListWidget`` icon
# cells. ``QIcon`` pixmaps are chosen to match ``setIconSize`` (bundled multi-resolution pack).
ICON_SIZE_PRESET_PIXELS: dict[str, int] = {
    "small": 16,
    "medium": 48,
    "large": 96,
    "xlarge": 256,
}

DEFAULT_ICON_SIZE_PRESET = "medium"


def normalize_icon_size_preset(raw: object) -> str:
    if isinstance(raw, str) and raw in ICON_SIZE_PRESET_PIXELS:
        return raw
    return DEFAULT_ICON_SIZE_PRESET


def icon_size_preset_to_qsize(preset: str) -> QSize:
    px = ICON_SIZE_PRESET_PIXELS.get(
        normalize_icon_size_preset(preset),
        ICON_SIZE_PRESET_PIXELS[DEFAULT_ICON_SIZE_PRESET],
    )
    return QSize(px, px)
