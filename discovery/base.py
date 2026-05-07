# File base.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Base protocol contract for discovery providers."""

from abc import ABC, abstractmethod
from collections.abc import Callable


class BaseDiscovery(ABC):
    def __init__(self, source: str) -> None:
        self.source = source
        self._callback: Callable[[str, dict], None] | None = None

    def set_callback(self, callback: Callable[[str, dict], None]) -> None:
        self._callback = callback

    def _emit(self, event_type: str, payload: dict) -> None:
        if self._callback is not None:
            self._callback(event_type, payload)

    @abstractmethod
    def start(self) -> None:
        """Start discovery listener."""

    @abstractmethod
    def stop(self) -> None:
        """Stop discovery listener."""

    @abstractmethod
    def refresh(self) -> None:
        """Trigger active discovery query."""
