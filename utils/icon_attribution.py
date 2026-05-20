# File icon_attribution.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Short attribution lines for device icons (About dialogs)."""

from __future__ import annotations

ICON_CREDIT_LINES_EN: tuple[str, ...] = (
    "Device-type icons: bundled under assets/icons/netneighbor; per-device overrides in ~/.config/netneighbor/custom_icons/.",
)


def icon_credit_lines_for_ui() -> tuple[str, ...]:
    """Bullet lines for About dialogs (GTK Credits / Qt About body)."""
    return ICON_CREDIT_LINES_EN


def icon_credits_paragraph() -> str:
    """Single paragraph for Qt ``QMessageBox.about`` or similar."""
    return "\n".join(ICON_CREDIT_LINES_EN)
