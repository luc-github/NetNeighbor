"""Device model used by the UI and discovery layers."""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class Device:
    name: str
    ip: str
    port: int
    type: str
    category: str
    source: str
    url: str | None = None
    metadata: dict = field(default_factory=dict)
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    online: bool = True
    icon: str | None = None

    @property
    def key(self) -> str:
        return f"{self.source}:{self.name.lower()}:{self.ip}:{self.port}:{self.type}"
