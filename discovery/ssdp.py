# File ssdp.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Minimal live SSDP discovery (M-SEARCH + response parsing)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ipaddress
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
from utils.discovery_cache import load_discovery_cache, save_discovery_cache
from utils.user_config_overlay import merge_ssdp_rules_overlays

_SSDP_ADDR = ("239.255.255.250", 1900)
_DEFAULT_TIMEOUT_SECONDS = 180
_REFRESH_INTERVAL_SECONDS = 60
_MAX_TIMEOUT_SECONDS = 3600
_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "ssdp_rules.json"
_RX_FRAME_DELIMITER = ">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>"
_TX_FRAME_DELIMITER = "<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<"
_XML_CACHE_MEMORY_TTL_SECONDS = 60
_XML_CACHE_DISK_TTL_SECONDS = 1800
_PROFILE_CACHE_DISK_TTL_SECONDS = 86400
_CACHE_FLUSH_INTERVAL_SECONDS = 3.0


def _descriptor_xml_host_key(location: str) -> str:
    """Group descriptor HTTP traffic by remote host: canonical IP literal or lowercase hostname.

    All XML GETs whose LOCATION URL resolves to the same IP share one min-interval bucket.
    """
    p = urlparse((location or "").strip())
    raw = (p.hostname or "").strip()
    if not raw:
        return ""
    inner = raw[1:-1] if raw.startswith("[") and raw.endswith("]") else raw
    try:
        addr = ipaddress.ip_address(inner)
        return addr.compressed
    except ValueError:
        return raw.lower()


class SSDPDiscovery(BaseDiscovery):
    def __init__(
        self,
        rules_enabled: bool = True,
        query_interval_seconds: int | None = None,
        mx_seconds: int | None = None,
        descriptor_http_min_interval_seconds: float | None = None,
    ) -> None:
        super().__init__(source="ssdp")
        self._logger = logging.getLogger(__name__)
        self._rules_enabled = bool(rules_enabled)
        self._refresh_interval_seconds = (
            int(query_interval_seconds) if query_interval_seconds is not None else _REFRESH_INTERVAL_SECONDS
        )
        mx = int(mx_seconds) if mx_seconds is not None else 5
        self._mx_seconds = max(5, min(6, mx))
        if descriptor_http_min_interval_seconds is None:
            self._descriptor_http_min_interval = 5.0
        else:
            self._descriptor_http_min_interval = max(0.0, float(descriptor_http_min_interval_seconds))
        self._last_descriptor_http_mono: dict[str, float] = {}
        self._xml_fetch_gates: dict[str, threading.Lock] = {}
        self._xml_fetch_gates_lock = threading.Lock()
        self._rules = self._load_rules()
        self._running = False
        self._socket: socket.socket | None = None
        self._listen_thread: threading.Thread | None = None
        self._gc_thread: threading.Thread | None = None
        self._refresh_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._seen_devices: dict[str, tuple[datetime, int, dict]] = {}
        self._xml_cache: dict[str, tuple[datetime, dict, str | None]] = {}
        self._xml_disk_cache: dict[str, tuple[datetime, dict, str | None]] = self._load_persistent_xml_cache()
        self._profile_disk_cache: dict[str, tuple[datetime, dict]] = self._load_persistent_profile_cache()
        self._cache_dirty = False
        self._last_cache_flush_monotonic = 0.0
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
        self._flush_persistent_caches_if_due(force=True)
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
                f"MX: {self._mx_seconds}\r\n"
                f"ST: {st}\r\n"
                "\r\n"
            ).encode("utf-8")
            try:
                sock.sendto(payload, _SSDP_ADDR)
                payload_text = payload.decode("utf-8", errors="ignore")
                self._logger.debug(
                    "SSDP TX %s:%s\n%s\n%s\n%s",
                    _SSDP_ADDR[0],
                    _SSDP_ADDR[1],
                    _TX_FRAME_DELIMITER,
                    payload_text,
                    _TX_FRAME_DELIMITER,
                )
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
            self._logger.debug(
                "SSDP RX from %s\n%s\n%s\n%s",
                addr[0],
                _RX_FRAME_DELIMITER,
                payload,
                _RX_FRAME_DELIMITER,
            )
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
            if self._stop_event.wait(timeout=self._refresh_interval_seconds):
                break
            if not self._running:
                break
            self._logger.debug("Periodic SSDP refresh tick")
            self._flush_persistent_caches_if_due()
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
        profile_key = self._profile_key_from_headers(headers, ip)
        profile = self._best_profile_for_headers(headers, ip)
        name = self._infer_name(server=server, st=st, ip=ip)
        device_type = self._infer_type(st, usn=usn, server=server, model_name=None, manufacturer=None)
        if profile is not None:
            _seen_at, prof = profile
            prof_name = prof.get("name")
            prof_type = prof.get("type")
            if isinstance(prof_name, str) and prof_name.strip():
                name = prof_name.strip()
            if isinstance(prof_type, str) and prof_type.strip():
                device_type = prof_type.strip()
        xml_fields, raw_xml = self._fetch_and_parse_xml(location)
        if not xml_fields and profile is not None:
            _seen_at, prof = profile
            prof_xml = prof.get("xml_fields")
            prof_raw = prof.get("raw_xml")
            if isinstance(prof_xml, dict):
                xml_fields = dict(prof_xml)
            if not raw_xml and isinstance(prof_raw, str) and prof_raw.strip():
                raw_xml = prof_raw
        if xml_fields.get("friendlyName"):
            name = xml_fields["friendlyName"]
        elif xml_fields.get("displayName"):
            name = xml_fields["displayName"]
        type_seed = xml_fields.get("deviceType") or st
        device_type = self._infer_type(
            type_seed,
            usn=usn,
            server=server,
            model_name=xml_fields.get("modelName"),
            manufacturer=xml_fields.get("manufacturer"),
            model_type=xml_fields.get("modelType"),
        )
        info_text = self._build_information_text(xml_fields)
        if info_text:
            self._logger.debug("SSDP info extracted for %s:%s -> %s", ip, port, info_text)
        if self._rules_enabled:
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
                    "modelType": xml_fields.get("modelType"),
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
        self._persist_profile_cache_entries(headers, ip, payload)
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
        model_type: str | None = None,
    ) -> str:
        if (model_type or "").strip().lower() == "nas":
            return "nas"
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
            "router": _("Routers & Gateways"),
            "mediaserver": _("Media Servers"),
            "printer": _("Printers"),
            "networkprinter": _("Printers"),
            "multifunction_printer": _("Printers"),
            "smartspeaker": _("Smart Speakers"),
            "smarttv": _("Smart TVs"),
            "smartdevice": _("Smart Devices"),
            "camera": _("Cameras"),
            "homeappliance": _("Home Appliances"),
            "cnc": _("CNC Machines"),
            "3dprinter": _("3D Printers"),
            "nas": _("NAS / File Servers"),
            "computer": _("Computers"),
            "unknown": _("Unknown Devices"),
        }.get(device_type, _("Unknown Devices"))

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

    def _profile_key_from_headers(self, headers: dict[str, str], fallback_ip: str) -> str:
        usn = headers.get("USN", "")
        if isinstance(usn, str) and usn.strip():
            usn_base = usn.strip().lower().split("::", 1)[0]
            if usn_base:
                return f"ssdp:usn:{usn_base}"
        location = headers.get("LOCATION")
        parsed = urlparse(location) if location else None
        ip = parsed.hostname if parsed and parsed.hostname else fallback_ip
        return f"ssdp:host:{str(ip).strip().lower()}"

    def _profile_candidate_keys(self, headers: dict[str, str], fallback_ip: str) -> list[str]:
        keys: list[str] = []
        seen: set[str] = set()

        def add(key: str) -> None:
            k = str(key).strip().lower()
            if not k or k in seen:
                return
            seen.add(k)
            keys.append(k)

        usn = headers.get("USN", "")
        if isinstance(usn, str) and usn.strip():
            usn_full = usn.strip().lower()
            add(f"ssdp:usn:{usn_full}")
            add(f"ssdp:usn:{usn_full.split('::', 1)[0]}")

        location = headers.get("LOCATION")
        parsed = urlparse(location) if location else None
        ip = parsed.hostname if parsed and parsed.hostname else fallback_ip
        ip_s = str(ip).strip().lower()
        if ip_s:
            add(f"ssdp:host:{ip_s}")
        return keys

    def _best_profile_for_headers(self, headers: dict[str, str], fallback_ip: str) -> tuple[datetime, dict] | None:
        best: tuple[datetime, dict] | None = None
        for key in self._profile_candidate_keys(headers, fallback_ip):
            cached = self._profile_disk_cache.get(key)
            if cached is None:
                continue
            if best is None or cached[0] > best[0]:
                best = cached
        return best

    def fetch_descriptor_xml(self, location_url: str | None) -> tuple[dict, str | None]:
        """Fetch and parse UPnP device descriptor XML (same caches as live SSDP LOCATION handling)."""
        return self._fetch_and_parse_xml(location_url)

    def _cached_xml_for_same_host(self, host_key: str, now: datetime) -> tuple[dict, str | None] | None:
        """Return freshest valid cache entry for any LOCATION whose host matches ``host_key``."""
        if not host_key:
            return None
        best: tuple[datetime, dict, str | None] | None = None
        for loc_key, tup in self._xml_cache.items():
            if _descriptor_xml_host_key(loc_key) != host_key:
                continue
            seen_at, fields, raw = tup
            if (now - seen_at) >= timedelta(seconds=_XML_CACHE_MEMORY_TTL_SECONDS):
                continue
            if best is None or seen_at > best[0]:
                best = (seen_at, dict(fields), raw)
        if best is not None:
            return best[1], best[2]
        for loc_key, tup in self._xml_disk_cache.items():
            if _descriptor_xml_host_key(loc_key) != host_key:
                continue
            seen_at, fields, raw = tup
            if (now - seen_at) < timedelta(seconds=_XML_CACHE_DISK_TTL_SECONDS):
                return dict(fields), raw
        return None

    def _fetch_and_parse_xml(self, location: str | None) -> tuple[dict, str | None]:
        if not location:
            return {}, None
        now = datetime.now(timezone.utc)
        memory_cached = self._xml_cache.get(location)
        if memory_cached is not None:
            seen_at, cached_fields, cached_raw = memory_cached
            if (now - seen_at) < timedelta(seconds=_XML_CACHE_MEMORY_TTL_SECONDS):
                return dict(cached_fields), cached_raw

        disk_cached = self._xml_disk_cache.get(location)
        if disk_cached is not None:
            seen_at, cached_fields, cached_raw = disk_cached
            if (now - seen_at) < timedelta(seconds=_XML_CACHE_DISK_TTL_SECONDS):
                self._xml_cache[location] = (now, dict(cached_fields), cached_raw)
                self._logger.debug("SSDP XML cache hit (disk) for %s", location)
                return dict(cached_fields), cached_raw

        host_key = _descriptor_xml_host_key(location)
        with self._xml_fetch_gates_lock:
            if host_key not in self._xml_fetch_gates:
                self._xml_fetch_gates[host_key] = threading.Lock()
            host_gate = self._xml_fetch_gates[host_key]
        with host_gate:
            now = datetime.now(timezone.utc)
            mem2 = self._xml_cache.get(location)
            if mem2 is not None:
                seen_at, cached_fields, cached_raw = mem2
                if (now - seen_at) < timedelta(seconds=_XML_CACHE_MEMORY_TTL_SECONDS):
                    return dict(cached_fields), cached_raw
            disk2 = self._xml_disk_cache.get(location)
            if disk2 is not None:
                seen_at, cached_fields, cached_raw = disk2
                if (now - seen_at) < timedelta(seconds=_XML_CACHE_DISK_TTL_SECONDS):
                    self._xml_cache[location] = (now, dict(cached_fields), cached_raw)
                    return dict(cached_fields), cached_raw

            mono = time.monotonic()
            if self._descriptor_http_min_interval > 0 and host_key:
                last_http = self._last_descriptor_http_mono.get(host_key)
                if last_http is not None and (mono - last_http) < self._descriptor_http_min_interval:
                    alt = self._cached_xml_for_same_host(host_key, now)
                    if alt is not None:
                        fields_a, raw_a = alt
                        self._xml_cache[location] = (now, dict(fields_a), raw_a)
                        self._logger.debug(
                            "SSDP descriptor coalesced (min-interval) for %s host=%s",
                            location,
                            host_key,
                        )
                        return dict(fields_a), raw_a
                    self._logger.debug(
                        "SSDP descriptor HTTP skipped (min-interval %.1fs, no cache for host %s) %s",
                        self._descriptor_http_min_interval,
                        host_key,
                        location,
                    )
                    return {}, None

            try:
                with urlopen(location, timeout=1.5) as response:
                    raw_xml = response.read().decode("utf-8", errors="ignore")
                    self._logger.debug("SSDP XML fetched from %s\n%s", location, raw_xml)
            except (URLError, OSError, TimeoutError):
                self._logger.debug("Failed to fetch SSDP XML at %s", location, exc_info=True)
                mem_f = self._xml_cache.get(location)
                if mem_f is not None:
                    _seen_at, cached_fields, cached_raw = mem_f
                    return dict(cached_fields), cached_raw
                disk_f = self._xml_disk_cache.get(location)
                if disk_f is not None:
                    _seen_at, cached_fields, cached_raw = disk_f
                    return dict(cached_fields), cached_raw
                return {}, None

            fields: dict = {}
            try:
                root = ET.fromstring(raw_xml)
                device_elem = self._find_first(root, "device")
                if device_elem is not None:
                    fields["friendlyName"] = self._find_text(device_elem, "friendlyName")
                    fields["deviceType"] = self._find_text(device_elem, "deviceType")
                    fields["manufacturer"] = self._find_text(device_elem, "manufacturer")
                    fields["manufacturerURL"] = self._find_text(device_elem, "manufacturerURL")
                    fields["modelName"] = self._find_text(device_elem, "modelName")
                    fields["modelType"] = self._find_text(device_elem, "modelType")
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
                if host_key:
                    self._last_descriptor_http_mono[host_key] = time.monotonic()
                return {}, raw_xml
            normalized = {k: v for k, v in fields.items() if v}
            self._xml_cache[location] = (now, normalized, raw_xml)
            self._xml_disk_cache[location] = (now, dict(normalized), raw_xml)
            if host_key:
                self._last_descriptor_http_mono[host_key] = time.monotonic()
            self._mark_cache_dirty()
            return dict(normalized), raw_xml

    def _load_persistent_xml_cache(self) -> dict[str, tuple[datetime, dict, str | None]]:
        out: dict[str, tuple[datetime, dict, str | None]] = {}
        cache_data = load_discovery_cache()
        raw = cache_data.get("ssdp_xml_cache")
        if not isinstance(raw, dict):
            return out
        entries = raw.get("entries")
        if not isinstance(entries, dict):
            return out
        now = datetime.now(timezone.utc)
        for location, row in entries.items():
            if not isinstance(location, str) or not location.strip() or not isinstance(row, dict):
                continue
            ts_text = row.get("updated_at")
            if not isinstance(ts_text, str) or not ts_text.strip():
                continue
            try:
                seen_at = datetime.fromisoformat(ts_text)
            except ValueError:
                continue
            if seen_at.tzinfo is None:
                seen_at = seen_at.replace(tzinfo=timezone.utc)
            if (now - seen_at) >= timedelta(seconds=_XML_CACHE_DISK_TTL_SECONDS):
                continue
            xml_fields = row.get("xml_fields")
            if not isinstance(xml_fields, dict):
                xml_fields = {}
            raw_xml = row.get("raw_xml")
            if not isinstance(raw_xml, str) or not raw_xml.strip():
                raw_xml = None
            out[location.strip()] = (seen_at, dict(xml_fields), raw_xml)
        return out

    def _build_persistent_xml_cache_entries(self) -> dict[str, dict]:
        entries: dict[str, dict] = {}
        now = datetime.now(timezone.utc)
        for location, cached in self._xml_disk_cache.items():
            if not isinstance(location, str) or not location.strip():
                continue
            seen_at, xml_fields, raw_xml = cached
            if (now - seen_at) >= timedelta(seconds=_XML_CACHE_DISK_TTL_SECONDS):
                continue
            entries[location] = {
                "updated_at": seen_at.isoformat(),
                "xml_fields": dict(xml_fields) if isinstance(xml_fields, dict) else {},
                "raw_xml": raw_xml if isinstance(raw_xml, str) else None,
            }
        return entries

    def _load_persistent_profile_cache(self) -> dict[str, tuple[datetime, dict]]:
        out: dict[str, tuple[datetime, dict]] = {}
        cache_data = load_discovery_cache()
        raw = cache_data.get("ssdp_profile_cache")
        if not isinstance(raw, dict):
            return out
        entries = raw.get("entries")
        if not isinstance(entries, dict):
            return out
        now = datetime.now(timezone.utc)
        for key, row in entries.items():
            if not isinstance(key, str) or not key.strip() or not isinstance(row, dict):
                continue
            ts_text = row.get("updated_at")
            if not isinstance(ts_text, str) or not ts_text.strip():
                continue
            try:
                seen_at = datetime.fromisoformat(ts_text)
            except ValueError:
                continue
            if seen_at.tzinfo is None:
                seen_at = seen_at.replace(tzinfo=timezone.utc)
            if (now - seen_at) >= timedelta(seconds=_PROFILE_CACHE_DISK_TTL_SECONDS):
                continue
            out[key.strip()] = (seen_at, dict(row))
        return out

    def _build_persistent_profile_cache_entries(self) -> dict[str, dict]:
        entries: dict[str, dict] = {}
        now = datetime.now(timezone.utc)
        for key, cached in self._profile_disk_cache.items():
            if not isinstance(key, str) or not key.strip():
                continue
            seen_at, row = cached
            if (now - seen_at) >= timedelta(seconds=_PROFILE_CACHE_DISK_TTL_SECONDS):
                continue
            payload = dict(row)
            payload["updated_at"] = seen_at.isoformat()
            entries[key] = payload
        return entries

    def _persist_profile_cache_entry(self, profile_key: str, payload: dict) -> None:
        if not isinstance(profile_key, str) or not profile_key.strip() or not isinstance(payload, dict):
            return
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        xml_fields = metadata.get("xml_fields") if isinstance(metadata, dict) else {}
        if not isinstance(xml_fields, dict):
            xml_fields = {}
        now = datetime.now(timezone.utc)
        ssdp_loc = metadata.get("location") if isinstance(metadata.get("location"), str) else ""
        row = {
            "name": payload.get("name"),
            "type": payload.get("type"),
            "category": payload.get("category"),
            "url": payload.get("url"),
            "xml_fields": dict(xml_fields),
            "raw_xml": metadata.get("xml") if isinstance(metadata, dict) else None,
            "ssdp_location": ssdp_loc.strip() if ssdp_loc.strip() else None,
        }
        self._profile_disk_cache[profile_key.strip()] = (now, row)
        self._mark_cache_dirty()

    def _persist_profile_cache_entries(self, headers: dict[str, str], fallback_ip: str, payload: dict) -> None:
        for key in self._profile_candidate_keys(headers, fallback_ip):
            self._persist_profile_cache_entry(key, payload)

    def _mark_cache_dirty(self) -> None:
        self._cache_dirty = True
        self._flush_persistent_caches_if_due()

    def _flush_persistent_caches_if_due(self, force: bool = False) -> None:
        if not self._cache_dirty and not force:
            return
        now_mono = time.monotonic()
        if not force and self._last_cache_flush_monotonic > 0:
            if (now_mono - self._last_cache_flush_monotonic) < _CACHE_FLUSH_INTERVAL_SECONDS:
                return

        xml_entries = self._build_persistent_xml_cache_entries()
        profile_entries = self._build_persistent_profile_cache_entries()
        save_discovery_cache(
            {
                "ssdp_xml_cache": {"version": 1, "entries": xml_entries},
                "ssdp_profile_cache": {"version": 1, "entries": profile_entries},
            }
        )

        self._xml_disk_cache = {
            loc: (datetime.fromisoformat(row["updated_at"]), row["xml_fields"], row.get("raw_xml"))
            for loc, row in xml_entries.items()
        }
        self._profile_disk_cache = {
            key: (datetime.fromisoformat(row["updated_at"]), dict(row))
            for key, row in profile_entries.items()
        }
        self._cache_dirty = False
        self._last_cache_flush_monotonic = now_mono

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
        if not self._rules_enabled:
            return ""
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
            str(xml_fields.get("modelType", "")),
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
