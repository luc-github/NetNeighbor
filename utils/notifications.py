"""Desktop notification helpers (best-effort)."""

from __future__ import annotations

from typing import Optional


def send_notification(app_name: str, title: str, message: str) -> None:
    """Send a desktop notification.

    Uses libnotify via Gtk/gi when available, otherwise falls back to `notify-send`.
    """

    # Try libnotify (via gi).
    try:
        import gi

        gi.require_version("Notify", "0.7")
        from gi.repository import Notify

        if not Notify.is_initted():
            Notify.init(app_name)
        n = Notify.Notification.new(title, message, None)
        n.show()
        return
    except Exception:
        pass

    # Fallback to notify-send.
    try:
        import subprocess

        subprocess.run(
            ["notify-send", title, message],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        # Last resort: do nothing.
        return

