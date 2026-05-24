# File macos_activation.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-24 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""macOS NSApplication activation-policy helpers — stubs only.

Dynamic policy switching (Accessory ↔ Regular) was tested but found to
interfere with QSystemTrayIcon availability on macOS 11 Big Sur when called
before the system tray is initialised.  These are no-ops until a reliable
sequencing solution is found.
"""

from __future__ import annotations


def set_policy_regular() -> None:
    pass


def set_policy_accessory() -> None:
    pass
