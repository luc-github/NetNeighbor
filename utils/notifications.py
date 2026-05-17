# File notifications.py for NetNeighbor version 1.0.0
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
