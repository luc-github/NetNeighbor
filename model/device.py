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
    monitored: bool = False
    icon: str | None = None

    @property
    def key(self) -> str:
        metadata = self.metadata if isinstance(self.metadata, dict) else {}
        if self.source == "ssdp":
            xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
            udn = xml_fields.get("UDN") or metadata.get("udn")
            if isinstance(udn, str) and udn.strip():
                return f"ssdp:udn:{udn.strip().lower()}"
            usn = metadata.get("usn")
            if isinstance(usn, str) and usn.strip():
                usn_base = usn.strip().lower().split("::", 1)[0]
                if usn_base:
                    return f"ssdp:usn:{usn_base}"
            mac = (
                xml_fields.get("mac")
                or metadata.get("mac")
                or metadata.get("mac_address")
                or metadata.get("MAC")
                or metadata.get("macAddress")
            )
            if isinstance(mac, str) and mac.strip():
                return f"ssdp:mac:{mac.strip().lower()}"
        return f"{self.source}:{self.ip}:{self.port}"
