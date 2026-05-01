"""Minimal live SSDP discovery (M-SEARCH + response parsing)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import socket
import threading
import time
from urllib.error import URLError
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen
import xml.etree.ElementTree as ET

from discovery.base import BaseDiscovery
from utils.user_config_overlay import merge_ssdp_rules_overlays

_SSDP_ADDR = ("239.255.255.250", 1900)
_DEFAULT_TIMEOUT_SECONDS = 180
_REFRESH_INTERVAL_SECONDS = 60
_MAX_TIMEOUT_SECONDS = 3600
_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "ssdp_rules.json"


class SSDPDiscovery(BaseDiscovery):
    def __init__(self) -> None:
        super().__init__(source="ssdp")
        self._logger = logging.getLogger(__name__)
        self._rules = self._load_rules()
        self._running = False
        self._socket: socket.socket | None = None
        self._listen_thread: threading.Thread | None = None
        self._gc_thread: threading.Thread | None = None
        self._refresh_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._seen_devices: dict[str, tuple[datetime, int, dict]] = {}
        self._xml_cache: dict[str, tuple[datetime, dict[str, str], str | None]] = {}
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._socket = self._create_socket()
        self._logger.info("SSDP discovery started")
        self._listen_thread = threading.Thread(target=self._listen_loop, name="ssdp-listener", daemon=True)
        self._listen_thread.start()
        self._gc_thread = threading.Thread(target=self._gc_loop, name="ssdp-gc", daemon=True)
        self._gc_thread.start()
        self._refresh_thread = threading.Thread(target=self._refresh_loop, name="ssdp-refresh", daemon=True)
        self._refresh_thread.start()
        self.refresh()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        sock = self._socket
        self._socket = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._listen_thread is not None:
            self._listen_thread.join(timeout=0.25)
        if self._gc_thread is not None:
            self._gc_thread.join(timeout=0.25)
        if self._refresh_thread is not None:
            self._refresh_thread.join(timeout=0.25)
        self._logger.info("SSDP discovery stopped")

    def refresh(self) -> None:
        if not self._running:
            return
        sock = self._socket
        if sock is None:
            return
        search_targets = ["ssdp:all", "upnp:rootdevice", "urn:dial-multiscreen-org:service:dial:1"]
        for st in search_targets:
            payload = (
                "M-SEARCH * HTTP/1.1\r\n"
                "HOST: 239.255.255.250:1900\r\n"
                "MAN: \"ssdp:discover\"\r\n"
                "MX: 2\r\n"
                f"ST: {st}\r\n"
                "\r\n"
            ).encode("utf-8")
            try:
                sock.sendto(payload, _SSDP_ADDR)
                self._logger.debug("SSDP TX %s:%s\n%s", _SSDP_ADDR[0], _SSDP_ADDR[1], payload.decode("utf-8", errors="ignore"))
            except OSError:
                self._logger.exception("Failed to send SSDP M-SEARCH for ST=%s", st)

    def _create_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
        sock.settimeout(0.2)
        try:
            sock.bind(("", 1900))
        except OSError:
            # Fallback when 1900 cannot be acquired.
            sock.bind(("", 0))
        try:
            membership = socket.inet_aton(_SSDP_ADDR[0]) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        except OSError:
            self._logger.debug("Unable to join SSDP multicast group", exc_info=True)
        return sock

    def _listen_loop(self) -> None:
        while self._running:
            sock = self._socket
            if sock is None:
                break
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            payload = data.decode("utf-8", errors="ignore")
            is_reply = payload.startswith("HTTP/1.1 200")
            is_notify = payload.startswith("NOTIFY * HTTP/1.1")
            if not (is_reply or is_notify):
                continue
            self._logger.debug("SSDP RX from %s\n%s", addr[0], payload)
            headers = self._parse_headers(payload)
            if not headers:
                continue
            if is_notify:
                nts = headers.get("NTS", "").lower()
                if nts == "ssdp:byebye":
                    offline_payload = self._build_notify_offline_payload(headers, addr[0])
                    self._logger.info(
                        "SSDP NOTIFY byebye: %s %s:%s",
                        offline_payload.get("name"),
                        offline_payload.get("ip"),
                        offline_payload.get("port"),
                    )
                    self._emit("device", offline_payload)
                    continue
                if nts and nts != "ssdp:alive":
                    continue
            device_payload = self._build_device_payload(headers, addr[0])
            self._logger.debug(
                "SSDP response from %s -> %s %s:%s",
                addr[0],
                device_payload.get("name"),
                device_payload.get("ip"),
                device_payload.get("port"),
            )
            device_key = self._device_key(device_payload)
            now = datetime.now(timezone.utc)
            timeout_seconds = self._timeout_seconds_for_payload(device_payload)
            with self._lock:
                self._seen_devices[device_key] = (now, timeout_seconds, device_payload)
            self._emit("device", device_payload)

    def _refresh_loop(self) -> None:
        while self._running:
            if self._stop_event.wait(timeout=_REFRESH_INTERVAL_SECONDS):
                break
            if not self._running:
                break
            self._logger.debug("Periodic SSDP refresh tick")
            self.refresh()

    def _gc_loop(self) -> None:
        while self._running:
            now = datetime.now(timezone.utc)
            expired: list[dict] = []
            with self._lock:
                for key, (seen, timeout_seconds, payload) in self._seen_devices.items():
                    if (now - seen) > timedelta(seconds=timeout_seconds):
                        expired.append(payload)
                for payload in expired:
                    key = self._device_key(payload)
                    self._seen_devices.pop(key, None)
            for payload in expired:
                offline_payload = dict(payload)
                offline_payload["online"] = False
                self._logger.debug(
                    "SSDP timeout -> offline %s %s:%s",
                    offline_payload.get("name"),
                    offline_payload.get("ip"),
                    offline_payload.get("port"),
                )
                self._emit("device", offline_payload)
            # Lightweight periodic scan.
            if self._stop_event.wait(timeout=2.0):
                break

    def _parse_headers(self, response: str) -> dict[str, str]:
        lines = response.split("\r\n")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().upper()] = value.strip()
        return headers

    def _build_device_payload(self, headers: dict[str, str], fallback_ip: str) -> dict:
        location = headers.get("LOCATION")
        parsed = urlparse(location) if location else None
        ip = parsed.hostname if parsed and parsed.hostname else fallback_ip
        port = parsed.port if parsed and parsed.port is not None else 80
        st = headers.get("ST") or headers.get("NT") or "ssdp:all"
        nt = headers.get("NT")
        server = headers.get("SERVER", "")
        usn = headers.get("USN", "")
        name = self._infer_name(server=server, st=st, ip=ip)
        device_type = self._infer_type(st, usn=usn, server=server, model_name=None, manufacturer=None)
        xml_fields, raw_xml = self._fetch_and_parse_xml(location)
        if xml_fields.get("friendlyName"):
            name = xml_fields["friendlyName"]
        elif xml_fields.get("displayName"):
            name = xml_fields["displayName"]
        if xml_fields.get("deviceType"):
            device_type = self._infer_type(
                xml_fields["deviceType"],
                usn=usn,
                server=server,
                model_name=xml_fields.get("modelName"),
                manufacturer=xml_fields.get("manufacturer"),
            )
        info_text = self._build_information_text(xml_fields)
        if info_text:
            self._logger.debug("SSDP info extracted for %s:%s -> %s", ip, port, info_text)
        rule_name = self._apply_name_rules(xml_fields)
        if rule_name:
            name = rule_name
        rule_type = self._apply_type_rules(headers=headers, xml_fields=xml_fields)
        if rule_type:
            device_type = rule_type
        presentation_url = xml_fields.get("presentationURL")
        services_description = self._build_services_description(xml_fields)
        payload = {
            "name": name,
            "ip": ip,
            "port": port,
            "type": device_type,
            "category": self._infer_category(device_type),
            "source": "ssdp",
            # Only use a real presentation URL from SSDP XML.
            "url": presentation_url if isinstance(presentation_url, str) and presentation_url.strip() else None,
            "metadata": {
                "location": location,
                "st": st,
                "nt": nt,
                "usn": usn,
                "server": server,
                "mac_address": xml_fields.get("mac"),
                "information": info_text,
                "cache_control": headers.get("CACHE-CONTROL"),
                "headers": headers,
                "xml_fields": {
                    "friendlyName": xml_fields.get("friendlyName"),
                    "deviceType": xml_fields.get("deviceType"),
                    "manufacturer": xml_fields.get("manufacturer"),
                    "manufacturerURL": xml_fields.get("manufacturerURL"),
                    "modelName": xml_fields.get("modelName"),
                    "displayName": xml_fields.get("displayName"),
                    "roomName": xml_fields.get("roomName"),
                    "modelURL": xml_fields.get("modelURL"),
                    "serialNumber": xml_fields.get("serialNumber"),
                    "mac": xml_fields.get("mac"),
                    "UDN": xml_fields.get("UDN"),
                    "presentationURL": xml_fields.get("presentationURL"),
                    "iconURL": xml_fields.get("iconURL"),
                    "icons_description": xml_fields.get("icons_description", "unavailable"),
                    "services_description": services_description or "unavailable",
                    "services_records": xml_fields.get("services_records"),
                },
                "xml": raw_xml,
            },
            "online": True,
        }
        self._logger.info(
            "SSDP device detected: %s %s:%s type=%s category=%s st=%s nt=%s",
            payload["name"],
            payload["ip"],
            payload["port"],
            payload["type"],
            payload["category"],
            st,
            nt,
        )
        return payload

    def _build_notify_offline_payload(self, headers: dict[str, str], fallback_ip: str) -> dict:
        st = headers.get("NT") or headers.get("ST") or "ssdp:all"
        usn = headers.get("USN", "")
        server = headers.get("SERVER", "")
        location = headers.get("LOCATION")
        parsed = urlparse(location) if location else None
        ip = parsed.hostname if parsed and parsed.hostname else fallback_ip
        port = parsed.port if parsed and parsed.port is not None else 80
        seen_payload = self._find_seen_payload_for_byebye(ip, usn)
        if seen_payload is not None:
            offline_payload = dict(seen_payload)
            offline_payload["online"] = False
            return offline_payload
        device_type = self._infer_type(st, usn=usn, server=server)
        return {
            "name": self._infer_name(server=server, st=st, ip=fallback_ip),
            "ip": ip,
            "port": port,
            "type": device_type,
            "category": self._infer_category(device_type),
            "source": "ssdp",
            "url": None,
            "metadata": {
                "location": location,
                "st": st,
                "nt": headers.get("NT"),
                "usn": usn,
                "server": server,
                "headers": headers,
            },
            "online": False,
        }

    def _find_seen_payload_for_byebye(self, ip: str, usn: str) -> dict | None:
        base_usn = usn.split("::", 1)[0].strip().lower()
        with self._lock:
            for _key, (_seen, _timeout_seconds, payload) in self._seen_devices.items():
                payload_ip = str(payload.get("ip", ""))
                if payload_ip != ip:
                    continue
                payload_usn = str(payload.get("metadata", {}).get("usn", "")).strip().lower()
                payload_base_usn = payload_usn.split("::", 1)[0]
                if base_usn and (payload_usn == usn.lower() or payload_base_usn == base_usn):
                    return dict(payload)
        return None

    def _timeout_seconds_for_payload(self, payload: dict) -> int:
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        cache_control = metadata.get("cache_control")
        max_age = None
        if isinstance(cache_control, str):
            value = cache_control.strip().lower()
            if "max-age=" in value:
                try:
                    max_age_text = value.split("max-age=", 1)[1].split(",", 1)[0].strip()
                    max_age = int(max_age_text)
                except ValueError:
                    max_age = None
        if max_age is None or max_age <= 0:
            return _DEFAULT_TIMEOUT_SECONDS
        timeout = max_age * 2
        return max(_DEFAULT_TIMEOUT_SECONDS, min(timeout, _MAX_TIMEOUT_SECONDS))

    def _infer_name(self, server: str, st: str, ip: str) -> str:
        if "router" in server.lower() or "wan" in st.lower():
            return f"Router {ip}"
        if "mediaserver" in st.lower() or "dlna" in server.lower():
            return f"Media Server {ip}"
        if "printer" in st.lower():
            return f"Printer {ip}"
        return f"SSDP Device {ip}"

    def _infer_type(
        self,
        st: str,
        usn: str = "",
        server: str = "",
        model_name: str | None = None,
        manufacturer: str | None = None,
    ) -> str:
        st_l = st.lower()
        usn_l = usn.lower()
        server_l = server.lower()
        model_l = (model_name or "").lower()
        manufacturer_l = (manufacturer or "").lower()
        combined = " ".join([st_l, usn_l, server_l, model_l, manufacturer_l])

        if "sonos" in combined:
            return "mediaserver"
        if (
            "mediaserver" in combined
            or "contentdirectory" in combined
            or "mediarenderer" in combined
            or "dial-multiscreen" in combined
            or "urn:dial-multiscreen-org:service:dial" in combined
            or "googletv" in combined
            or "android tv" in combined
            or "chromecast" in combined
        ):
            return "mediaserver"
        if (
            "wan" in combined
            or "internetgatewaydevice" in combined
            or "router" in combined
            or "gateway" in combined
            or "wifialliance" in combined
            or "wfadevice" in combined
            or "wfawlanconfig" in combined
        ):
            return "router"
        if "nas" in combined or "synology" in combined or "qnap" in combined:
            return "nas"
        if "printer" in combined:
            return "printer"
        if "basic" in combined or "computer" in combined:
            return "computer"
        return "unknown"

    def _infer_category(self, device_type: str) -> str:
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
            "unknown": "Unknown Devices",
        }.get(device_type, "Unknown Devices")

    def _device_key(self, payload: dict) -> str:
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
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

        ip = str(payload.get("ip", "0.0.0.0"))
        port = int(payload.get("port", 0))
        return f"ssdp:endpoint:{ip}:{port}"

    def _fetch_and_parse_xml(self, location: str | None) -> tuple[dict[str, str], str | None]:
        if not location:
            return {}, None
        now = datetime.now(timezone.utc)
        cached = self._xml_cache.get(location)
        if cached is not None:
            seen_at, cached_fields, cached_raw = cached
            if (now - seen_at) < timedelta(seconds=60):
                return dict(cached_fields), cached_raw
        try:
            with urlopen(location, timeout=1.5) as response:
                raw_xml = response.read().decode("utf-8", errors="ignore")
                self._logger.debug("SSDP XML fetched from %s\n%s", location, raw_xml)
        except (URLError, OSError, TimeoutError):
            self._logger.debug("Failed to fetch SSDP XML at %s", location, exc_info=True)
            return {}, None

        fields: dict[str, str] = {}
        try:
            root = ET.fromstring(raw_xml)
            device_elem = self._find_first(root, "device")
            if device_elem is not None:
                fields["friendlyName"] = self._find_text(device_elem, "friendlyName")
                fields["deviceType"] = self._find_text(device_elem, "deviceType")
                fields["manufacturer"] = self._find_text(device_elem, "manufacturer")
                fields["manufacturerURL"] = self._find_text(device_elem, "manufacturerURL")
                fields["modelName"] = self._find_text(device_elem, "modelName")
                fields["displayName"] = self._find_text(device_elem, "displayName")
                fields["roomName"] = self._find_text(device_elem, "roomName")
                fields["modelURL"] = self._find_text(device_elem, "modelURL")
                fields["serialNumber"] = self._find_text(device_elem, "serialNumber")
                fields["mac"] = self._find_text(device_elem, "MACAddress") or self._find_text(device_elem, "mac")
                fields["UDN"] = self._find_text(device_elem, "UDN")
                fields["presentationURL"] = self._find_text(device_elem, "presentationURL")
                fields["icons_description"] = self._build_icons_description(device_elem)
                fields["services_description"] = self._build_services_description_from_device(device_elem)
                fields["services_records"] = self._build_services_records(device_elem, location)
                fields["iconURL"] = self._build_preferred_icon_url(device_elem, location)
        except ET.ParseError:
            self._logger.debug("Invalid SSDP XML received from %s", location, exc_info=True)
            return {}, raw_xml
        normalized = {k: v for k, v in fields.items() if v}
        self._xml_cache[location] = (now, normalized, raw_xml)
        return dict(normalized), raw_xml

    def _build_services_description(self, xml_fields: dict[str, str]) -> str:
        return xml_fields.get("services_description", "")

    def _build_services_description_from_device(self, device_elem: ET.Element) -> str:
        services: list[str] = []
        for elem in device_elem.iter():
            if elem.tag.rsplit("}", 1)[-1] == "serviceType":
                value = (elem.text or "").strip()
                if value:
                    services.append(value)
        return ", ".join(services)

    def _build_services_records(self, device_elem: ET.Element, location: str | None) -> list[dict[str, str]]:
        parsed_location = urlparse(location or "")
        target_default = parsed_location.hostname or ""
        port_default = str(parsed_location.port) if parsed_location.port is not None else ""
        records: list[dict[str, str]] = []
        for service_elem in self._find_elements(device_elem, "service"):
            service_type = self._find_text(service_elem, "serviceType")
            if not service_type:
                continue
            control_url = self._find_text(service_elem, "controlURL")
            parsed_control = urlparse(urljoin(location or "", control_url)) if control_url else None
            target = target_default
            port = port_default
            if parsed_control is not None:
                if parsed_control.hostname:
                    target = parsed_control.hostname
                if parsed_control.port is not None:
                    port = str(parsed_control.port)
            records.append(
                {
                    "service": service_type,
                    "target": target or "unavailable",
                    "port": port or "unavailable",
                }
            )
        return records

    def _build_icons_description(self, device_elem: ET.Element) -> str:
        items: list[str] = []
        current: dict[str, str] = {}
        for elem in device_elem.iter():
            local = elem.tag.rsplit("}", 1)[-1]
            if local in {"mimetype", "width", "height", "url"}:
                current[local] = (elem.text or "").strip()
            if local == "icon":
                if current:
                    items.append(
                        f"mimetype={current.get('mimetype', '')};"
                        f"width={current.get('width', '')};"
                        f"height={current.get('height', '')};"
                        f"url={current.get('url', '')}"
                    )
                current = {}
        return ", ".join([item for item in items if item.strip(";,")])

    def _build_preferred_icon_url(self, device_elem: ET.Element, location: str | None) -> str:
        base_url = location or ""
        best_url = ""
        best_area = -1
        for icon_elem in self._find_elements(device_elem, "icon"):
            icon_url = self._find_text(icon_elem, "url")
            if not icon_url:
                continue
            width_txt = self._find_text(icon_elem, "width")
            height_txt = self._find_text(icon_elem, "height")
            try:
                area = int(width_txt or "0") * int(height_txt or "0")
            except ValueError:
                area = 0
            if area >= best_area:
                best_area = area
                best_url = urljoin(base_url, icon_url)
        return best_url

    def _find_first(self, root: ET.Element, local_name: str) -> ET.Element | None:
        for elem in root.iter():
            if elem.tag.rsplit("}", 1)[-1] == local_name:
                return elem
        return None

    def _find_elements(self, root: ET.Element, local_name: str) -> list[ET.Element]:
        return [elem for elem in root.iter() if elem.tag.rsplit("}", 1)[-1] == local_name]

    def _find_text(self, root: ET.Element, local_name: str) -> str:
        for elem in root.iter():
            if elem.tag.rsplit("}", 1)[-1] == local_name:
                return (elem.text or "").strip()
        return ""

    def _load_rules(self) -> dict:
        default_rules = {
            "name_rules": {
                "fallback_fields": ["friendlyName", "displayName"],
                "prefer_display_name_when_no_friendly_name": True,
            },
            "information_rules": {
                "concat_fields": ["roomName", "displayName"],
                "separator": " / ",
            },
            "type_rules": [
                {"contains_any": ["smartspeaker-audio", "sonos"], "type": "mediaserver"},
                {"contains_any": ["dial-multiscreen", "chromecast", "googletv", "android tv"], "type": "mediaserver"},
                {"contains_any": ["wifialliance", "wfadevice", "wfawlanconfig"], "type": "router"},
                {"contains_any": ["synology", "qnap", "nas"], "type": "nas"},
            ],
        }
        try:
            if not _RULES_PATH.exists():
                return merge_ssdp_rules_overlays(default_rules)
            parsed = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                return merge_ssdp_rules_overlays(default_rules)
            merged = dict(default_rules)
            merged.update(parsed)
            self._logger.info("Loaded SSDP rules from %s", _RULES_PATH)
            return merge_ssdp_rules_overlays(merged)
        except (OSError, json.JSONDecodeError):
            self._logger.exception("Failed to load SSDP rules, using defaults")
            return merge_ssdp_rules_overlays(default_rules)

    def _apply_name_rules(self, xml_fields: dict[str, str]) -> str:
        rules = self._rules.get("name_rules", {})
        if not isinstance(rules, dict):
            return ""
        fallback_fields = rules.get("fallback_fields", [])
        if not isinstance(fallback_fields, list):
            fallback_fields = []
        friendly = str(xml_fields.get("friendlyName", "")).strip()
        display = str(xml_fields.get("displayName", "")).strip()
        if rules.get("prefer_display_name_when_no_friendly_name") and (not friendly) and display:
            return display
        for field in fallback_fields:
            value = xml_fields.get(str(field))
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _build_information_text(self, xml_fields: dict[str, str]) -> str:
        rules = self._rules.get("information_rules", {})
        if not isinstance(rules, dict):
            return ""
        fields = rules.get("concat_fields", [])
        if not isinstance(fields, list):
            fields = []
        separator = str(rules.get("separator", " / "))
        values: list[str] = []
        for field in fields:
            value = xml_fields.get(str(field))
            if isinstance(value, str) and value.strip():
                value_norm = value.strip()
                if value_norm not in values:
                    values.append(value_norm)
        return separator.join(values)

    def _apply_type_rules(self, headers: dict[str, str], xml_fields: dict[str, str]) -> str:
        rule_list = self._rules.get("type_rules", [])
        if not isinstance(rule_list, list):
            return ""
        searchable_parts = [
            str(headers.get("ST", "")),
            str(headers.get("NT", "")),
            str(headers.get("USN", "")),
            str(headers.get("SERVER", "")),
            str(xml_fields.get("deviceType", "")),
            str(xml_fields.get("manufacturer", "")),
            str(xml_fields.get("modelName", "")),
            str(xml_fields.get("friendlyName", "")),
            str(xml_fields.get("displayName", "")),
            str(xml_fields.get("roomName", "")),
        ]
        haystack = " ".join(searchable_parts).lower()
        for rule in rule_list:
            if not isinstance(rule, dict):
                continue
            target_type = rule.get("type")
            contains_any = rule.get("contains_any", [])
            if not isinstance(target_type, str) or not isinstance(contains_any, list):
                continue
            for token in contains_any:
                token_norm = str(token).strip().lower()
                if token_norm and token_norm in haystack:
                    return target_type.strip().lower()
        return ""
