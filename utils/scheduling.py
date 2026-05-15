# File scheduling.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-13
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Schedule callables onto the UI toolkit main loop (GTK idle, Qt queued slot, etc.)."""

from __future__ import annotations

from collections.abc import Callable

# Run *fn* on the toolkit UI thread when GTK is available (GLib main loop).
ScheduleMainFn = Callable[[Callable[[], None]], None]


def gtk_idle_schedule(fn: Callable[[], None]) -> None:
    """Marshal *fn* to the GTK main loop via ``GLib.idle_add``."""
    from gi.repository import GLib

    def _wrap() -> bool:
        fn()
        return False

    GLib.idle_add(_wrap)
