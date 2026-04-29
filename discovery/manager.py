"""Orchestrates all protocol providers and keeps a simple device cache."""

from collections.abc import Callable
from datetime import datetime, timezone
import logging

from discovery.base import BaseDiscovery
from discovery.mdns import MDNSDiscovery
from discovery.ssdp import SSDPDiscovery
from model.device import Device


class DiscoveryManager:
    def __init__(self, demo_mode: bool = False) -> None:
        self._logger = logging.getLogger(__name__)
        self._protocols: list[BaseDiscovery] = [SSDPDiscovery(), MDNSDiscovery()]
        self._devices: dict[str, Device] = {}
        self._listeners: list[Callable[[list[Device]], None]] = []
        self._type_overrides: dict[str, str] = {}
        self._monitored_overrides: dict[str, bool] = {}
        self._last_seen_overrides: dict[str, str] = {}
        self._demo_mode = demo_mode

        for protocol in self._protocols:
            protocol.set_callback(self._on_protocol_event)

    def add_listener(self, callback: Callable[[list[Device]], None]) -> None:
        self._listeners.append(callback)
        callback(self.devices)

    @property
    def devices(self) -> list[Device]:
        return sorted(self._devices.values(), key=lambda device: (device.category, device.name.lower()))

    def start(self) -> None:
        self._logger.info("Starting discovery protocols: %s", [p.source for p in self._protocols])
        for protocol in self._protocols:
            protocol.start()

    def stop(self) -> None:
        self._logger.info("Stopping discovery protocols")
        for protocol in self._protocols:
            protocol.stop()

    def refresh(self) -> None:
        self._logger.debug("Manual refresh requested")
        for protocol in self._protocols:
            protocol.refresh()

    def add_or_update_device(self, device: Device) -> None:
        override_key = self._make_override_key_for_device(device)
        existing_seen = self._last_seen_overrides.get(override_key)
        if not existing_seen:
            existing_seen = self._last_seen_overrides.get(self._make_override_key(device.source, device.ip, device.port))
        if device.online:
            now = datetime.now(timezone.utc)
            device.last_seen = now
            self._last_seen_overrides[override_key] = now.isoformat()
        elif existing_seen:
            try:
                device.last_seen = datetime.fromisoformat(existing_seen)
            except ValueError:
                pass

        self._apply_type_override(device)
        self._apply_monitored_override(device)
        existing_key = device.key
        existing = self._devices.get(existing_key)
        if existing is None and device.source == "ssdp":
            existing_key, existing = self._find_existing_ssdp_by_endpoint(device)

        if existing is not None:
            # Preserve user-follow choice across updates.
            device.monitored = existing.monitored
            if device.online is False and existing.last_seen:
                device.last_seen = existing.last_seen
            if device.source == "ssdp":
                self._logger.debug(
                    "SSDP merge candidate for %s:%s old_name=%s new_name=%s",
                    device.ip,
                    device.port,
                    existing.name,
                    device.name,
                )
                device.metadata = self._merge_ssdp_metadata(existing.metadata, device.metadata, existing.name, device.name)
                device.name = self._pick_ssdp_name(existing.name, device.name, existing.metadata, device.metadata)
                device.icon = existing.icon or device.icon
                if not device.url and existing.url:
                    device.url = existing.url
                if existing_key != device.key and existing_key in self._devices:
                    del self._devices[existing_key]
                existing_key = device.key

        self._devices[existing_key] = device
        self._logger.info(
            "Device %s: source=%s name=%s ip=%s port=%s type=%s category=%s online=%s",
            "updated" if existing else "added",
            device.source,
            device.name,
            device.ip,
            device.port,
            device.type,
            device.category,
            device.online,
        )
        self._logger.debug(
            "Device %s: %s %s:%s [%s] online=%s category=%s",
            "updated" if existing else "added",
            device.source,
            device.ip,
            device.port,
            device.name,
            device.online,
            device.category,
        )
        self._notify()

    def set_type_overrides(self, overrides: dict[str, str]) -> None:
        normalized: dict[str, str] = {}
        for key, value in overrides.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            value_norm = value.strip().lower()
            if value_norm:
                normalized[key] = value_norm
        self._type_overrides = normalized

    def get_type_overrides(self) -> dict[str, str]:
        return dict(self._type_overrides)

    def set_monitored_overrides(self, overrides: dict[str, bool]) -> None:
        normalized: dict[str, bool] = {}
        for key, value in overrides.items():
            if isinstance(key, str):
                normalized[key] = bool(value)
        self._monitored_overrides = normalized

    def get_monitored_overrides(self) -> dict[str, bool]:
        return dict(self._monitored_overrides)

    def set_last_seen_overrides(self, overrides: dict[str, str]) -> None:
        normalized: dict[str, str] = {}
        for key, value in overrides.items():
            if isinstance(key, str) and isinstance(value, str) and value.strip():
                normalized[key] = value.strip()
        self._last_seen_overrides = normalized

    def get_last_seen_overrides(self) -> dict[str, str]:
        return dict(self._last_seen_overrides)

    def restore_monitored_snapshots(self, snapshots: list[dict]) -> None:
        if not isinstance(snapshots, list):
            return
        changed = False
        for raw in snapshots:
            if not isinstance(raw, dict):
                continue
            source = str(raw.get("source", "unknown")).strip().lower()
            ip = str(raw.get("ip", "")).strip()
            port_raw = raw.get("port", 0)
            try:
                port = int(port_raw)
            except (TypeError, ValueError):
                continue
            if not ip or port <= 0:
                continue
            metadata = raw.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            device = Device(
                name=str(raw.get("name", "Unknown")) or "Unknown",
                ip=ip,
                port=port,
                type=str(raw.get("type", "unknown")) or "unknown",
                category=str(raw.get("category", "Unknown Devices")) or "Unknown Devices",
                source=source or "unknown",
                url=raw.get("url") if isinstance(raw.get("url"), str) else None,
                metadata=metadata,
                online=False,
                monitored=True,
                icon=raw.get("icon") if isinstance(raw.get("icon"), str) else None,
            )
            seen_text = raw.get("last_seen")
            if isinstance(seen_text, str) and seen_text.strip():
                try:
                    device.last_seen = datetime.fromisoformat(seen_text.strip())
                except ValueError:
                    pass
            self._apply_type_override(device)
            self._apply_monitored_override(device)
            if not device.monitored:
                continue
            existing = self._devices.get(device.key)
            if existing is not None and existing.online:
                continue
            self._devices[device.key] = device
            changed = True
        if changed:
            self._notify()

    def set_device_type_override(self, source: str, ip: str, port: int, device_type: str | None) -> None:
        changed = False
        for old_key, existing in list(self._devices.items()):
            if existing.source != source or existing.ip != ip or existing.port != port:
                continue
            preferred_key = self._make_override_key_for_device(existing)
            endpoint_key = self._make_override_key(source, ip, port)
            if device_type is None or not str(device_type).strip() or str(device_type).strip().lower() == "auto":
                self._type_overrides.pop(preferred_key, None)
                self._type_overrides.pop(endpoint_key, None)
            else:
                self._type_overrides[preferred_key] = str(device_type).strip().lower()
            self._apply_type_override(existing)
            new_key = existing.key
            if new_key != old_key:
                self._devices.pop(old_key, None)
                self._devices[new_key] = existing
            changed = True
        if changed:
            self._notify()

    def _merge_ssdp_metadata(self, old_meta: dict, new_meta: dict, old_name: str, new_name: str) -> dict:
        old_meta = old_meta if isinstance(old_meta, dict) else {}
        new_meta = new_meta if isinstance(new_meta, dict) else {}
        merged = dict(old_meta)
        merged.update(new_meta)

        old_xml_fields = old_meta.get("xml_fields") if isinstance(old_meta.get("xml_fields"), dict) else {}
        new_xml_fields = new_meta.get("xml_fields") if isinstance(new_meta.get("xml_fields"), dict) else {}
        old_rank = self._ssdp_profile_rank(old_xml_fields)
        new_rank = self._ssdp_profile_rank(new_xml_fields)
        preferred_xml_fields = old_xml_fields if old_rank >= new_rank else new_xml_fields
        self._logger.debug(
            "SSDP profile rank old=%s new=%s selected=%s",
            old_rank,
            new_rank,
            "old" if old_rank >= new_rank else "new",
        )

        merged_xml_fields = dict(old_xml_fields)
        for key, value in new_xml_fields.items():
            if value is None:
                continue
            if isinstance(value, str):
                value_norm = value.strip()
                if not value_norm or value_norm.lower() == "unavailable":
                    continue
                old_value = merged_xml_fields.get(key)
                if not old_value:
                    merged_xml_fields[key] = value_norm
                    continue
                # Keep richer data when two payloads disagree (e.g. Sonos speaker group vs full root description).
                if len(value_norm) > len(str(old_value).strip()):
                    merged_xml_fields[key] = value_norm
            else:
                merged_xml_fields[key] = value

        # Strict preference for the richer/profile-ranked XML fields.
        for strict_key in ("friendlyName", "deviceType", "iconURL", "icons_description"):
            preferred_value = preferred_xml_fields.get(strict_key)
            if isinstance(preferred_value, str) and preferred_value.strip():
                merged_xml_fields[strict_key] = preferred_value.strip()

        merged["xml_fields"] = merged_xml_fields

        # Track alternate names instead of overwriting main identity.
        aliases = merged.get("alternate_names")
        if not isinstance(aliases, list):
            aliases = []
        for candidate in (old_name, new_name):
            if not isinstance(candidate, str):
                continue
            value = candidate.strip()
            if not value or value in aliases:
                continue
            aliases.append(value)
            self._logger.debug("SSDP alias tracked: %s", value)
        merged["alternate_names"] = aliases

        # Keep last known information text when a partial payload does not provide it.
        old_info = old_meta.get("information")
        new_info = new_meta.get("information")
        if isinstance(new_info, str) and new_info.strip():
            merged["information"] = new_info.strip()
        elif isinstance(old_info, str) and old_info.strip():
            merged["information"] = old_info.strip()

        old_xml = old_meta.get("xml")
        new_xml = new_meta.get("xml")
        if isinstance(old_xml, str) and isinstance(new_xml, str):
            if old_rank >= new_rank and len(old_xml.strip()) >= len(new_xml.strip()):
                merged["xml"] = old_xml
                self._logger.debug("SSDP raw XML preserved from previous richer profile")
        elif isinstance(old_xml, str) and not isinstance(new_xml, str):
            merged["xml"] = old_xml
            self._logger.debug("SSDP raw XML preserved because new payload has no XML")

        return merged

    def _ssdp_profile_rank(self, xml_fields: dict) -> int:
        if not isinstance(xml_fields, dict):
            return 0
        device_type = str(xml_fields.get("deviceType", "")).lower()
        score = 0
        if "schemas-upnp-org" in device_type:
            score += 4
        if "smartspeaker-audio" in device_type:
            score -= 1
        if isinstance(xml_fields.get("iconURL"), str) and xml_fields.get("iconURL", "").strip():
            score += 1
        if isinstance(xml_fields.get("manufacturer"), str) and xml_fields.get("manufacturer", "").strip():
            score += 1
        if isinstance(xml_fields.get("modelName"), str) and xml_fields.get("modelName", "").strip():
            score += 1
        return score

    def _pick_ssdp_name(self, old_name: str, new_name: str, old_meta: dict, new_meta: dict) -> str:
        old_name = old_name.strip() if isinstance(old_name, str) else ""
        new_name = new_name.strip() if isinstance(new_name, str) else ""
        if not old_name:
            return new_name or "Unknown"
        if not new_name:
            return old_name

        if old_name.startswith(("SSDP Device ", "Router ", "Media Server ", "Printer ")) and not new_name.startswith(
            ("SSDP Device ", "Router ", "Media Server ", "Printer ")
        ):
            return new_name

        old_fields = old_meta.get("xml_fields") if isinstance(old_meta, dict) and isinstance(old_meta.get("xml_fields"), dict) else {}
        new_fields = new_meta.get("xml_fields") if isinstance(new_meta, dict) and isinstance(new_meta.get("xml_fields"), dict) else {}
        if self._ssdp_profile_rank(new_fields) > self._ssdp_profile_rank(old_fields):
            self._logger.debug("SSDP name switched to newer profile name: %s", new_name)
            return new_name
        self._logger.debug("SSDP name preserved from previous profile: %s", old_name)
        return old_name

    def _find_existing_ssdp_by_endpoint(self, candidate: Device) -> tuple[str, Device] | tuple[None, None]:
        candidate_mac = self._extract_mac(candidate.metadata)
        for key, item in self._devices.items():
            if item.source != "ssdp":
                continue
            if item.ip != candidate.ip or item.port != candidate.port:
                continue
            existing_mac = self._extract_mac(item.metadata)
            if candidate_mac and existing_mac and candidate_mac != existing_mac:
                self._logger.debug(
                    "SSDP endpoint match rejected due to MAC mismatch ip=%s port=%s old=%s new=%s",
                    candidate.ip,
                    candidate.port,
                    existing_mac,
                    candidate_mac,
                )
                continue
            if candidate_mac and not existing_mac:
                self._logger.debug(
                    "SSDP endpoint match accepted and upgraded with MAC ip=%s port=%s mac=%s",
                    candidate.ip,
                    candidate.port,
                    candidate_mac,
                )
            if existing_mac and not candidate_mac:
                self._logger.debug(
                    "SSDP endpoint match accepted using existing MAC ip=%s port=%s mac=%s",
                    candidate.ip,
                    candidate.port,
                    existing_mac,
                )
                return key, item
            return key, item
        return None, None

    def _extract_mac(self, metadata: dict) -> str:
        if not isinstance(metadata, dict):
            return ""
        xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
        mac = (
            xml_fields.get("mac")
            or metadata.get("mac")
            or metadata.get("mac_address")
            or metadata.get("MAC")
            or metadata.get("macAddress")
        )
        if not isinstance(mac, str):
            return ""
        return mac.strip().lower()

    def _apply_type_override(self, device: Device) -> None:
        override_type = self._find_override_value(self._type_overrides, device)
        if not override_type:
            return
        device.type = override_type
        device.category = self._category_for_type(override_type)

    def _apply_monitored_override(self, device: Device) -> None:
        override_value = self._find_override_value(self._monitored_overrides, device)
        if override_value is not None:
            device.monitored = bool(override_value)

    def _find_override_value(self, store: dict, device: Device):
        preferred_key = self._make_override_key_for_device(device)
        if preferred_key in store:
            return store[preferred_key]
        endpoint_key = self._make_override_key(device.source, device.ip, device.port)
        return store.get(endpoint_key)

    def _make_override_key(self, source: str, ip: str, port: int) -> str:
        return f"{source}:{ip}:{int(port)}"

    def _make_override_key_for_device(self, device: Device) -> str:
        mac = self._extract_mac(device.metadata)
        if mac:
            return f"{device.source}:mac:{mac}"
        return self._make_override_key(device.source, device.ip, device.port)

    def _category_for_type(self, device_type: str) -> str:
        return {
            "router": "Routers & Gateways",
            "mediaserver": "Media Servers",
            "printer": "Printers",
            "networkprinter": "Printers",
            "smartspeaker": "Smart Speakers",
            "smarttv": "Smart TVs",
            "smartdevice": "Smart Devices",
            "camera": "Cameras",
            "homeappliance": "Home Appliances",
            "cnc": "CNC Machines",
            "3dprinter": "3D Printers",
            "nas": "NAS / File Servers",
            "computer": "Computers",
            "esp32": "ESP3D Devices",
            "unknown": "Unknown Devices",
        }.get(device_type, "Unknown Devices")

    def set_device_monitored(self, device_key: str, monitored: bool) -> None:
        device = self._devices.get(device_key)
        if device is None:
            return
        if device.monitored == monitored:
            return
        device.monitored = monitored
        self._monitored_overrides[self._make_override_key_for_device(device)] = monitored
        self._notify()

    def _notify(self) -> None:
        snapshot = self.devices
        self._logger.debug("Publishing %d devices to %d listeners", len(snapshot), len(self._listeners))
        for listener in self._listeners:
            listener(snapshot)

    def _on_protocol_event(self, event_type: str, payload: dict) -> None:
        if event_type != "device":
            self._logger.debug("Ignoring protocol event_type=%s", event_type)
            return

        device = Device(
            name=payload.get("name", "Unknown"),
            ip=payload.get("ip", "0.0.0.0"),
            port=int(payload.get("port", 0)),
            type=payload.get("type", "unknown"),
            category=payload.get("category", "Unknown Devices"),
            source=payload.get("source", "unknown"),
            url=payload.get("url"),
            metadata=payload.get("metadata", {}),
            online=bool(payload.get("online", True)),
            icon=payload.get("icon"),
        )
        self.add_or_update_device(device)

    def _load_demo_devices(self) -> None:
        demo_devices = [
            Device(
                name="ESP3D Printer Node",
                ip="192.168.1.42",
                port=80,
                type="esp32",
                category="ESP3D Devices",
                source="mdns",
                url="http://192.168.1.42:80/",
                metadata={
                    "hostname": "esp3d-printer.local.",
                    "server": "esp3d-printer.local.",
                    "interface": "wlan0",
                    "priority": 0,
                    "weight": 0,
                    "ttl": 120,
                    "services": [
                        {
                            "service": "_esp3d._tcp.local.",
                            "port": 80,
                            "hostname": "esp3d-printer.local.",
                            "server": "esp3d-printer.local.",
                        },
                        {
                            "service": "_arduino._tcp.local.",
                            "port": 81,
                            "hostname": "esp3d-printer.local.",
                            "server": "esp3d-printer.local.",
                        },
                    ],
                    "txt": {
                        "board": "ESP32",
                        "fw": "ESP3D 3.0.2",
                        "path": "/",
                        "auth": "false",
                    },
                },
                online=True,
                icon="esp32.png",
            ),
            Device(
                name="Salon Media Server",
                ip="192.168.1.15",
                port=8200,
                type="mediaserver",
                category="Media Servers",
                source="ssdp",
                url="http://192.168.1.15:8200/",
                metadata={
                    "st": "urn:schemas-upnp-org:device:MediaServer:1",
                    "nt": "urn:schemas-upnp-org:device:MediaServer:1",
                    "usn": "uuid:media-server-01::urn:schemas-upnp-org:device:MediaServer:1",
                    "location": "http://192.168.1.15:8200/rootDesc.xml",
                    "server": "Linux/6.8 UPnP/1.1 ReadyMedia/1.3.2",
                    "cache_control": "max-age=1800",
                    "headers": {
                        "HOST": "239.255.255.250:1900",
                        "CACHE-CONTROL": "max-age=1800",
                        "LOCATION": "http://192.168.1.15:8200/rootDesc.xml",
                        "NT": "urn:schemas-upnp-org:device:MediaServer:1",
                        "NTS": "ssdp:alive",
                        "SERVER": "Linux/6.8 UPnP/1.1 ReadyMedia/1.3.2",
                        "USN": "uuid:media-server-01::urn:schemas-upnp-org:device:MediaServer:1",
                    },
                    "xml_fields": {
                        "friendlyName": "Salon Media Server",
                        "deviceType": "urn:schemas-upnp-org:device:MediaServer:1",
                        "manufacturer": "ReadyMedia",
                        "manufacturerURL": "https://www.readymedia.org/",
                        "modelName": "MiniDLNA",
                        "modelURL": "https://www.readymedia.org/docs/",
                        "serialNumber": "RM-001-ABCD",
                        "UDN": "uuid:media-server-01",
                        "presentationURL": "http://192.168.1.15:8200/",
                        "services_description": "urn:schemas-upnp-org:service:ContentDirectory:1, urn:schemas-upnp-org:service:ConnectionManager:1",
                        "icons_description": "mimetype=image/png;width=48;height=48;url=/icons/icon48.png",
                    },
                    "xml": """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <URLBase>http://192.168.1.15:8200/</URLBase>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>Salon Media Server</friendlyName>
    <manufacturer>ReadyMedia</manufacturer>
    <modelName>MiniDLNA</modelName>
    <UDN>uuid:media-server-01</UDN>
  </device>
</root>""",
                },
                online=True,
                icon="mediaserver.png",
            ),
            Device(
                name="Box Internet",
                ip="192.168.1.1",
                port=80,
                type="router",
                category="Routers & Gateways",
                source="ssdp",
                url="http://192.168.1.1:80/",
                metadata={
                    "st": "urn:schemas-upnp-org:device:WANDevice:1",
                    "nt": "urn:schemas-upnp-org:device:WANDevice:1",
                    "usn": "uuid:router-01::urn:schemas-upnp-org:device:WANDevice:1",
                    "location": "http://192.168.1.1:80/igd.xml",
                    "server": "Linux/5.15 UPnP/1.0 RouterOS/7.10",
                    "cache_control": "max-age=1200",
                    "headers": {
                        "HOST": "239.255.255.250:1900",
                        "CACHE-CONTROL": "max-age=1200",
                        "LOCATION": "http://192.168.1.1:80/igd.xml",
                        "NT": "urn:schemas-upnp-org:device:WANDevice:1",
                        "NTS": "ssdp:alive",
                        "SERVER": "Linux/5.15 UPnP/1.0 RouterOS/7.10",
                        "USN": "uuid:router-01::urn:schemas-upnp-org:device:WANDevice:1",
                    },
                    "xml_fields": {
                        "friendlyName": "Box Internet",
                        "deviceType": "urn:schemas-upnp-org:device:WANDevice:1",
                        "manufacturer": "ISP Vendor",
                        "manufacturerURL": "unavailable",
                        "modelName": "Gateway X1",
                        "modelURL": "unavailable",
                        "serialNumber": "GWX1-7788",
                        "UDN": "uuid:router-01",
                        "presentationURL": "http://192.168.1.1/",
                        "services_description": "urn:schemas-upnp-org:service:WANIPConnection:1",
                        "icons_description": "unavailable",
                    },
                    "xml": """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:WANDevice:1</deviceType>
    <friendlyName>Box Internet</friendlyName>
    <manufacturer>ISP Vendor</manufacturer>
    <modelName>Gateway X1</modelName>
    <UDN>uuid:router-01</UDN>
  </device>
</root>""",
                },
                online=True,
                icon="router.png",
            ),
            Device(
                name="NAS Atelier",
                ip="192.168.1.20",
                port=2049,
                type="nas",
                category="NAS / File Servers",
                source="mdns",
                url=None,
                metadata={
                    "hostname": "nas-atelier.local.",
                    "server": "nas-atelier.local.",
                    "interface": "eth0",
                    "priority": 0,
                    "weight": 0,
                    "ttl": 4500,
                    "services": [
                        {
                            "service": "_nfs._tcp.local.",
                            "port": 2049,
                            "hostname": "nas-atelier.local.",
                            "server": "nas-atelier.local.",
                        },
                        {
                            "service": "_mountd._tcp.local.",
                            "port": 32768,
                            "hostname": "nas-atelier.local.",
                            "server": "nas-atelier.local.",
                        },
                    ],
                    "txt": {
                        "model": "DS224+",
                        "vendor": "Synology",
                        "path": "/volume1",
                    },
                },
                online=False,
                icon="nas.png",
            ),
        ]
        for device in demo_devices:
            self._devices[device.key] = device
        self._notify()
