"""mDNS discovery placeholder implementation."""

from discovery.base import BaseDiscovery


class MDNSDiscovery(BaseDiscovery):
    def __init__(self) -> None:
        super().__init__(source="mdns")
        self._running = False

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def refresh(self) -> None:
        if not self._running:
            return
