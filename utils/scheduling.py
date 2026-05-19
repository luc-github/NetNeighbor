# File scheduling.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Schedule callables onto the UI toolkit main loop."""

from __future__ import annotations

from collections.abc import Callable

ScheduleMainFn = Callable[[Callable[[], None]], None]
