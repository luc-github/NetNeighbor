# File notifications.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Desktop notification helpers (best-effort, Linux only via notify-send)."""

from __future__ import annotations


def send_notification(app_name: str, title: str, message: str) -> None:
    try:
        import subprocess
        subprocess.run(
            ["notify-send", "--app-name", app_name, title, message],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
