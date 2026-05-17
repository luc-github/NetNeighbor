# File scheduling.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Schedule callables onto the UI toolkit main loop."""

from __future__ import annotations

from collections.abc import Callable

ScheduleMainFn = Callable[[Callable[[], None]], None]
