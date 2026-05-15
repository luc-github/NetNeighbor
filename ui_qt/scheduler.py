# File scheduler.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Marshal callables from discovery threads onto the GUI main thread (PySide6)."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class MainThreadScheduler(QObject):
    """Queue plain callables to run on the thread that owns this QObject (typically the GUI thread)."""

    invoke = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.invoke.connect(self._dispatch)

    @Slot(object)
    def _dispatch(self, fn: object) -> None:
        if callable(fn):
            fn()
