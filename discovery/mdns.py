"""mDNS discovery using zeroconf service browsing."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import socket

from discovery.base import BaseDiscovery

try:
    from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
except Exception:  # pragma: no cover - dependency/runtime availability
    ServiceBrowser = None
    ServiceListener = object
    Zeroconf = None


_TYPE_MAP_PATH = Path(__file__).resolve().parent.parent / "data" / "device_types.json"


class _MDNSListener(ServiceListener):
    def __init__(self, discovery: "MDNSDiscovery") -> None:
        self._discovery = discovery

    def add_service(self, zc, service_type: str, name: str) -> None:
        self._discovery._on_service_change(zc, service_type, name, online=True)

    def update_service(self, zc, service_type: str, name: str) -> None:
        self._discovery._on_service_change(zc, service_type, name, online=True)

    def remove_service(self, _zc, service_type: str, name: str) -> None:
        self._discovery._on_service_remove(service_type, name)


class MDNSDiscovery(BaseDiscovery):
    def __init__(self) -> None:
        super().__init__(source="mdns")
        self._logger = logging.getLogger(__name__)
        self._running = False
        self._zeroconf = None
        self._browsers: list[ServiceBrowser] = []
        self._listener = _MDNSListener(self)
        self._seen_by_service: dict[tuple[str, str], dict] = {}
        self._service_host_keys: dict[tuple[str, str], str] = {}
        self._type_map = self._load_mdns_type_map()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        if Zeroconf is None or ServiceBrowser is None:
            self._logger.warning("mDNS discovery unavailable: zeroconf not installed")
            return
        try:
            self._zeroconf = Zeroconf()
        except Exception:
            self._logger.exception("Failed to initialize zeroconf")
            self._running = False
            return

        for service_type in self._service_types_to_browse():
            try:
                browser = ServiceBrowser(self._zeroconf, service_type, self._listener)
                self._browsers.append(browser)
                self._logger.debug("mDNS browser started for %s", service_type)
            except Exception:
                self._logger.exception("Failed to start mDNS browser for %s", service_type)
        if not self._browsers:
            self._logger.warning("No mDNS service browser started")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._seen_by_service.clear()
        self._browsers.clear()
        zc = self._zeroconf
        self._zeroconf = None
        if zc is not None:
            try:
                zc.close()
            except Exception:
                self._logger.debug("Error while closing zeroconf", exc_info=True)

    def refresh(self) -> None:
        # Passive protocol: browsing callbacks keep state fresh.
        if not self._running:
            return
        self._logger.debug("mDNS refresh requested (passive browse)")

    def _service_types_to_browse(self) -> list[str]:
        known_types = sorted(self._type_map.keys()) if self._type_map else []
        if not known_types:
            known_types = ["_http._tcp"]
        return [self._normalize_service_type(service) for service in known_types]

    def _normalize_service_type(self, service: str) -> str:
        value = (service or "").strip().lower()
        if not value:
            return "_http._tcp.local."
        if value.endswith(".local."):
            return value
        if value.endswith(".local"):
            return f"{value}."
        return f"{value}.local."

    def _load_mdns_type_map(self) -> dict[str, dict]:
        try:
            parsed = json.loads(_TYPE_MAP_PATH.read_text(encoding="utf-8"))
            mdns_map = parsed.get("mdns") if isinstance(parsed, dict) else {}
            if isinstance(mdns_map, dict):
                normalized: dict[str, dict] = {}
                for key, value in mdns_map.items():
                    if isinstance(key, str) and isinstance(value, dict):
                        normalized[key.strip().lower()] = value
                return normalized
        except (OSError, json.JSONDecodeError):
            self._logger.exception("Failed to load mDNS type map from %s", _TYPE_MAP_PATH)
        return {}

    def _on_service_change(self, zc, service_type: str, name: str, online: bool) -> None:
        if not self._running:
            return
        try:
            info = zc.get_service_info(service_type, name, timeout=1200)
        except Exception:
            self._logger.debug("mDNS lookup failed for %s %s", service_type, name, exc_info=True)
            return
        if info is None:
            self._logger.debug("mDNS service info unavailable for %s %s", service_type, name)
            return
        payload = self._build_payload(service_type, name, info, online=online)
        key = (service_type, name)
        self._seen_by_service[key] = payload
        host_key = self._host_key_for_payload(payload, service_type, name)
        self._service_host_keys[key] = host_key
        aggregate = self._aggregate_payload_for_host(host_key, online=True)
        self._emit("device", aggregate)

    def _on_service_remove(self, service_type: str, name: str) -> None:
        if not self._running:
            return
        key = (service_type, name)
        self._seen_by_service.pop(key, None)
        host_key = self._service_host_keys.pop(key, "")
        if host_key:
            still_online = any(value == host_key for value in self._service_host_keys.values())
            if still_online:
                self._emit("device", self._aggregate_payload_for_host(host_key, online=True))
                return
            payload = self._aggregate_payload_for_host(host_key, online=False)
            self._emit("device", payload)
            return
        payload = self._build_fallback_remove_payload(service_type, name)
        self._emit("device", payload)

    def _build_payload(self, service_type: str, name: str, info, online: bool) -> dict:
        service_key = self._service_key(service_type)
        mapping = self._type_map.get(service_key, {})
        addresses = self._extract_addresses(info)
        ip = addresses[0] if addresses else "0.0.0.0"
        port = int(getattr(info, "port", 0) or 0)
        hostname = getattr(info, "name", "") or name
        server = getattr(info, "server", "")
        priority = int(getattr(info, "priority", 0) or 0)
        weight = int(getattr(info, "weight", 0) or 0)
        txt = self._decode_properties(getattr(info, "properties", {}))

        display_name = self._infer_display_name(name, txt)
        type_name = str(mapping.get("type", "unknown")).strip().lower() or "unknown"
        type_name = self._infer_type_from_context(type_name, service_key, display_name, txt)
        category = self._category_for_type(
            type_name,
            str(mapping.get("category", "Unknown Devices")) or "Unknown Devices",
        )
        icon = mapping.get("icon")
        if not isinstance(icon, str):
            icon = self._icon_for_type(type_name)

        service_normalized = self._normalize_service_type(service_type)
        metadata = {
            "service": service_normalized,
            "hostname": hostname,
            "server": server,
            "priority": priority,
            "weight": weight,
            "ttl": int(getattr(info, "host_ttl", 0) or getattr(info, "other_ttl", 0) or 0),
            "services": [
                {
                    "service": service_normalized,
                    "port": port,
                    "hostname": hostname,
                    "server": server,
                }
            ],
            "txt": txt,
        }
        return {
            "name": display_name,
            "ip": ip,
            "port": port,
            "type": type_name,
            "category": category,
            "source": "mdns",
            "url": None,
            "metadata": metadata,
            "online": bool(online),
            "icon": icon,
        }

    def _build_fallback_remove_payload(self, service_type: str, name: str) -> dict:
        service_key = self._service_key(service_type)
        mapping = self._type_map.get(service_key, {})
        type_name = str(mapping.get("type", "unknown")).strip().lower() or "unknown"
        type_name = self._infer_type_from_context(type_name, service_key, self._infer_display_name(name, {}), {})
        category = self._category_for_type(
            type_name,
            str(mapping.get("category", "Unknown Devices")) or "Unknown Devices",
        )
        icon = mapping.get("icon")
        if not isinstance(icon, str):
            icon = self._icon_for_type(type_name)
        return {
            "name": self._infer_display_name(name, {}),
            "ip": "0.0.0.0",
            "port": int(mapping.get("default_port", 0) or 0),
            "type": type_name,
            "category": category,
            "source": "mdns",
            "url": None,
            "metadata": {"service": self._normalize_service_type(service_type), "txt": {}, "services": []},
            "online": False,
            "icon": icon,
        }

    def _service_key(self, service_type: str) -> str:
        value = self._normalize_service_type(service_type)
        if value.endswith(".local."):
            value = value[: -len(".local.")]
        return value

    def _infer_type_from_service(self, service_key: str) -> str:
        if "_esp3d._tcp" in service_key or "_arduino._tcp" in service_key:
            return "esp32"
        if "_smb._tcp" in service_key:
            return "computer"
        if "_printer._tcp" in service_key or "_ipp._tcp" in service_key:
            return "networkprinter"
        if "_http._tcp" in service_key:
            return "http"
        return "unknown"

    def _infer_type_from_context(
        self,
        mapped_type: str,
        service_key: str,
        display_name: str,
        txt: dict[str, str],
    ) -> str:
        base = mapped_type if mapped_type and mapped_type != "unknown" else self._infer_type_from_service(service_key)
        # Normalize legacy printer label to avoid duplicate "Printer" vs "Network Printer" classes in mDNS.
        if base == "printer":
            base = "networkprinter"

        haystack_parts = [service_key, display_name]
        haystack_parts.extend([f"{k}={v}" for k, v in txt.items()])
        haystack = " ".join([part.lower() for part in haystack_parts if isinstance(part, str)])

        if "fluidnc" in haystack:
            return "cnc"
        if "laserjet" in haystack:
            return "networkprinter"
        if "synology" in haystack or "qnap" in haystack or " nas " in f" {haystack} ":
            return "nas"
        return base

    def _infer_display_name(self, service_instance: str, txt: dict[str, str]) -> str:
        for key in ("name", "friendlyname", "friendly_name", "device", "model"):
            value = txt.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        label = service_instance.split("._", 1)[0].strip()
        return label or "mDNS Device"

    def _extract_addresses(self, info) -> list[str]:
        addresses: list[str] = []
        parsed_addresses = getattr(info, "parsed_addresses", None)
        if callable(parsed_addresses):
            try:
                values = parsed_addresses()
                if isinstance(values, list):
                    addresses.extend([str(v) for v in values if v])
            except Exception:
                pass
        raw_addrs = getattr(info, "addresses", None)
        if isinstance(raw_addrs, list):
            for raw in raw_addrs:
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    if len(raw) == 4:
                        addresses.append(socket.inet_ntop(socket.AF_INET, raw))
                    elif len(raw) == 16:
                        addresses.append(socket.inet_ntop(socket.AF_INET6, raw))
                except OSError:
                    continue
        unique: list[str] = []
        for value in addresses:
            if value not in unique:
                unique.append(value)
        return unique

    def _decode_properties(self, props) -> dict[str, str]:
        if not isinstance(props, dict):
            return {}
        decoded: dict[str, str] = {}
        for raw_key, raw_value in props.items():
            key = self._to_text(raw_key).strip()
            if not key:
                continue
            decoded[key] = self._to_text(raw_value).strip()
        return decoded

    def _to_text(self, value) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def _host_key_for_payload(self, payload: dict, service_type: str, name: str) -> str:
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        ip = str(payload.get("ip", "")).strip()
        if ip and ip != "0.0.0.0":
            return ip
        for key in ("hostname", "server"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        return f"{self._normalize_service_type(service_type)}::{name.strip().lower()}"

    def _aggregate_payload_for_host(self, host_key: str, online: bool) -> dict:
        entries: list[dict] = []
        for key, value in self._seen_by_service.items():
            if self._service_host_keys.get(key) == host_key:
                entries.append(value)
        if not entries:
            return {
                "name": "mDNS Device",
                "ip": "0.0.0.0",
                "port": 0,
                "type": "unknown",
                "category": "Unknown Devices",
                "source": "mdns",
                "url": None,
                "metadata": {"service": "_mdns._udp.local.", "txt": {}, "services": []},
                "online": bool(online),
                "icon": self._icon_for_type("unknown"),
            }

        def _score(entry: dict) -> tuple[int, int]:
            metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
            service_text = str(metadata.get("service", "")).lower()
            if "_http._tcp" in service_text:
                return (0, 0)
            if "_esp3d._tcp" in service_text:
                return (1, 0)
            return (2, int(entry.get("port", 0) or 0))

        representative = sorted(entries, key=_score)[0]
        rep_metadata = representative.get("metadata") if isinstance(representative.get("metadata"), dict) else {}
        merged_services: list[dict] = []
        for entry in entries:
            metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
            services = metadata.get("services") if isinstance(metadata.get("services"), list) else []
            for service in services:
                if not isinstance(service, dict):
                    continue
                normalized_service = str(service.get("service", "")).strip()
                normalized_port = int(service.get("port", 0) or 0)
                duplicate = any(
                    str(existing.get("service", "")).strip() == normalized_service
                    and int(existing.get("port", 0) or 0) == normalized_port
                    for existing in merged_services
                )
                if not duplicate:
                    merged_services.append(dict(service))

        http_port = None
        for service in merged_services:
            service_name = str(service.get("service", "")).lower()
            if "_http._tcp" in service_name:
                candidate = int(service.get("port", 0) or 0)
                if candidate > 0:
                    http_port = candidate
                    break

        ip = str(representative.get("ip", "0.0.0.0"))
        url = None
        if http_port and ip and ip != "0.0.0.0":
            url = f"http://{ip}:{http_port}/"

        merged_metadata = dict(rep_metadata)
        merged_metadata["services"] = merged_services

        return {
            "name": representative.get("name", "mDNS Device"),
            "ip": ip,
            "port": int(representative.get("port", 0) or 0),
            "type": representative.get("type", "unknown"),
            "category": representative.get("category", "Unknown Devices"),
            "source": "mdns",
            "url": url,
            "metadata": merged_metadata,
            "online": bool(online),
            "icon": representative.get("icon"),
        }

    def _build_url(self, mapping: dict, ip: str, port: int) -> str | None:
        info_url = mapping.get("info_url")
        if not isinstance(info_url, str) or not info_url.strip():
            return None
        try:
            return info_url.format(ip=ip, port=port)
        except Exception:
            return None

    def _category_for_type(self, device_type: str, fallback: str) -> str:
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
            "http": "Unknown Devices",
            "unknown": "Unknown Devices",
        }.get(device_type, fallback or "Unknown Devices")

    def _icon_for_type(self, device_type: str) -> str | None:
        return {
            "router": "router.png",
            "mediaserver": "mediaserver.png",
            "printer": "printer.png",
            "networkprinter": "printer.png",
            "smartspeaker": "smartspeaker.png",
            "smarttv": "smarttv.png",
            "smartdevice": "smartdevice.png",
            "camera": "camera.png",
            "homeappliance": "homeappliance.png",
            "cnc": "cnc.png",
            "3dprinter": "3dprinter.png",
            "nas": "nas.png",
            "computer": "computer.png",
            "esp32": "esp32.png",
            "http": "http.png",
            "unknown": "unknown.png",
        }.get(device_type)
