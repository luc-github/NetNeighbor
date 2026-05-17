# File device.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
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
    hidden: bool = False
    icon: str | None = None

    @property
    def key(self) -> str:
        metadata = self.metadata if isinstance(self.metadata, dict) else {}
        if self.source == "ssdp":
            xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
            # Prefer SSDP USN (unique per advertisement) over XML UDN — bundled descriptors
            # (e.g. Sonos group XML) can repeat the same UDN across different speakers.
            usn = metadata.get("usn")
            if isinstance(usn, str) and usn.strip():
                usn_base = usn.strip().lower().split("::", 1)[0]
                if usn_base:
                    return f"ssdp:usn:{usn_base}"
            # Profile-cache bootstrap rows often have no USN on disk; never collapse by duplicate UDN.
            if metadata.get("from_ssdp_cache"):
                ip_s = str(self.ip).strip()
                if ip_s and ip_s not in {"", "0.0.0.0"}:
                    return f"ssdp:cache:{ip_s}:{int(self.port)}"
            udn = xml_fields.get("UDN") or metadata.get("udn")
            if isinstance(udn, str) and udn.strip():
                return f"ssdp:udn:{udn.strip().lower()}"
            mac = (
                xml_fields.get("mac")
                or metadata.get("mac")
                or metadata.get("mac_address")
                or metadata.get("MAC")
                or metadata.get("macAddress")
            )
            if isinstance(mac, str) and mac.strip():
                return f"ssdp:mac:{mac.strip().lower()}"
        if self.source == "wsd":
            epr = metadata.get("wsd_epr")
            if isinstance(epr, str) and epr.strip():
                return f"wsd:epr:{epr.strip().lower()}"
        if self.source == "wsdd":
            uri = metadata.get("wsdd_uri")
            if isinstance(uri, str) and uri.strip():
                return f"wsdd:uri:{uri.strip().lower()}"
        # NetBIOS names are not guaranteed unique on the LAN (multi-boot, misconfig). Key by endpoint.
        return f"{self.source}:{self.ip}:{self.port}"
