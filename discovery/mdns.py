# File mdns.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""mDNS discovery using zeroconf service browsing."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import socket
import threading

from discovery.base import BaseDiscovery
from utils.mdns_rules import cached_mdns_rules, evaluate_type_rules
from utils.scheduling import ScheduleMainFn
from utils.user_config_overlay import USER_DEVICE_TYPES_JSON, merge_device_types_trees, optional_user_json

ServiceBrowser = None
ServiceListener = object
Zeroconf = None
ZeroconfServiceTypes = None

_ENUMERATION_TIMEOUT_S = 2.5
_ENUMERATION_REFRESH_INTERVAL_S = 240
# How long to retain a per-service record after remove_service() when the host
# is still alive via other services.  In practice, genuine mid-session service
# removal (NAS disabling FTP, etc.) is extremely rare; almost all remove_service
# callbacks while a host is online are TTL re-announcement jitter or transient
# network hiccups.  A refresh() restarts browsers and actively re-queries, so
# stale records that truly disappeared will be cleaned up on the next user-
# initiated reload even if this timer hasn't fired.  Set long enough to survive
# the worst-case zeroconf re-query window (~4 × re-query intervals ≈ 2-3 min).
_SERVICE_REMOVE_GRACE_S = 180


_TYPE_MAP_PATH = Path(__file__).resolve().parent.parent / "data" / "device_types.json"
_ZEROCONF_LIBS_LOADED = False


def _ensure_zeroconf_libs() -> None:
    global ServiceBrowser, Zeroconf, ZeroconfServiceTypes, _ZEROCONF_LIBS_LOADED
    if _ZEROCONF_LIBS_LOADED:
        return
    _ZEROCONF_LIBS_LOADED = True
    try:
        from zeroconf import ServiceBrowser as _SB, Zeroconf as _Z
        ServiceBrowser = _SB
        Zeroconf = _Z
    except Exception:
        return
    try:
        from zeroconf import ZeroconfServiceTypes as _ZST
        ZeroconfServiceTypes = _ZST
    except Exception:
        pass


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
    def __init__(
        self,
        rules_enabled: bool = True,
        *,
        enumeration_timeout_seconds: float | None = None,
        enumeration_interval_seconds: int | None = None,
        service_info_timeout_ms: int | None = None,
        schedule_on_main_thread: ScheduleMainFn | None = None,
    ) -> None:
        super().__init__(source="mdns")
        self._logger = logging.getLogger(__name__)
        self._rules_enabled = bool(rules_enabled)
        self._enumeration_timeout_s = (
            float(enumeration_timeout_seconds)
            if enumeration_timeout_seconds is not None
            else _ENUMERATION_TIMEOUT_S
        )
        self._enumeration_interval_s = (
            int(enumeration_interval_seconds)
            if enumeration_interval_seconds is not None
            else _ENUMERATION_REFRESH_INTERVAL_S
        )
        self._service_info_timeout_ms = (
            int(service_info_timeout_ms) if service_info_timeout_ms is not None else 2500
        )
        self._mdns_rules = cached_mdns_rules() if self._rules_enabled else {"type_rules": [], "summary_fields": []}
        self._running = False
        self._zeroconf = None
        self._browsers: list[ServiceBrowser] = []
        self._browsers_started: set[str] = set()
        self._listener = _MDNSListener(self)
        self._seen_by_service: dict[tuple[str, str], dict] = {}
        self._service_host_keys: dict[tuple[str, str], str] = {}
        self._host_last_endpoint: dict[str, tuple[str, int]] = {}
        self._type_map = self._load_mdns_type_map()
        self._schedule_main: ScheduleMainFn = (
            schedule_on_main_thread if schedule_on_main_thread is not None else (lambda fn: fn())
        )
        self._enumeration_timer: threading.Timer | None = None
        self._enumeration_busy = False
        self._pending_remove_timers: dict[tuple[str, str], threading.Timer] = {}

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        _ensure_zeroconf_libs()
        if Zeroconf is None or ServiceBrowser is None:
            self._logger.warning("mDNS discovery unavailable: zeroconf not installed")
            return
        try:
            self._zeroconf = Zeroconf()
        except Exception:
            self._logger.exception("Failed to initialize zeroconf")
            self._running = False
            return

        self._browsers_started.clear()
        for service_type in self._service_types_to_browse_from_config():
            self._ensure_browser(service_type)
        self._kick_service_type_enumeration()
        self._schedule_enumeration_timer()
        if not self._browsers:
            self._logger.warning("No mDNS service browser started")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._enumeration_busy = False
        if self._enumeration_timer is not None:
            try:
                self._enumeration_timer.cancel()
            except Exception:
                pass
            self._enumeration_timer = None
        for key in list(self._pending_remove_timers):
            self._cancel_grace_remove(key)
        self._seen_by_service.clear()
        self._host_last_endpoint.clear()
        self._browsers.clear()
        self._browsers_started.clear()
        zc = self._zeroconf
        self._zeroconf = None
        if zc is not None:
            try:
                zc.close()
            except Exception:
                self._logger.debug("Error while closing zeroconf", exc_info=True)

    def refresh(self) -> None:
        if not self._running or self._zeroconf is None:
            return
        # Cancel pending grace-remove timers before dropping the cache.
        for key in list(self._pending_remove_timers):
            self._cancel_grace_remove(key)
        # Cancel running browsers.
        for browser in self._browsers:
            try:
                if hasattr(browser, "cancel"):
                    browser.cancel()
            except Exception:
                pass
        self._browsers.clear()
        # Close and reopen the Zeroconf instance to clear its internal DNS cache.
        # This ensures the subsequent PTR queries go out without "known answer"
        # suppression headers (RFC 6762 §7.1), so devices that stopped announcing
        # (e.g. a NAS whose TTL expired) will respond and re-populate the device list.
        old_zc = self._zeroconf
        self._zeroconf = None
        if old_zc is not None:
            try:
                old_zc.close()
            except Exception:
                self._logger.debug("mDNS refresh: error closing zeroconf", exc_info=True)
        try:
            self._zeroconf = Zeroconf()
        except Exception:
            self._logger.exception("mDNS refresh: failed to reopen zeroconf")
            return
        self._logger.debug("mDNS refresh: zeroconf cache cleared, sending fresh PTR queries")
        types_to_rebrowse = list(self._browsers_started)
        self._browsers_started.clear()
        for service_type in types_to_rebrowse:
            self._ensure_browser(service_type)
        self._kick_service_type_enumeration()

    def _service_types_to_browse_from_config(self) -> list[str]:
        known_types = sorted(self._type_map.keys()) if self._type_map else []
        if not known_types:
            known_types = ["_http._tcp"]
        return [self._normalize_service_type(service) for service in known_types]

    def _ensure_browser(self, service_type: str) -> None:
        if not self._running or self._zeroconf is None or ServiceBrowser is None:
            return
        normalized = self._normalize_service_type(service_type)
        if normalized in self._browsers_started:
            return
        try:
            browser = ServiceBrowser(self._zeroconf, normalized, self._listener)
            self._browsers.append(browser)
            self._browsers_started.add(normalized)
            self._logger.debug("mDNS browser started for %s", normalized)
        except Exception:
            self._logger.exception("Failed to start mDNS browser for %s", normalized)

    def _restart_browsers(self) -> None:
        """Cancel all running browsers and recreate them to force fresh PTR queries.

        A new ServiceBrowser sends an immediate PTR query for its type; devices
        respond and trigger add_service callbacks — recovering any records that
        zeroconf evicted from its cache without re-announcement.

        Pending grace-remove timers are also cancelled: the fresh PTR queries
        will either confirm the service is gone (no add_service callback) or
        restore it (add_service fires and refreshes the record).
        """
        for key in list(self._pending_remove_timers):
            self._cancel_grace_remove(key)
        for browser in self._browsers:
            try:
                if hasattr(browser, "cancel"):
                    browser.cancel()
            except Exception:
                pass
        self._browsers.clear()
        types_to_rebrowse = list(self._browsers_started)
        self._browsers_started.clear()
        for service_type in types_to_rebrowse:
            self._ensure_browser(service_type)
        self._logger.debug("mDNS browsers restarted for %d type(s)", len(types_to_rebrowse))

    def _enqueue_browsers_for_types(self, service_types: list[str]) -> None:
        """Register browsers on the UI main thread via ``schedule_on_main_thread`` when provided."""

        def apply_pending() -> None:
            if not self._running or self._zeroconf is None:
                return
            for service_type in service_types:
                self._ensure_browser(service_type)

        self._schedule_main(apply_pending)

    def _kick_service_type_enumeration(self) -> None:
        """DNS-SD: discover which service types are advertised on the LAN (beyond config file)."""

        if ZeroconfServiceTypes is None or not self._running:
            return
        if self._enumeration_busy:
            self._logger.debug("mDNS enumeration skipped (already in progress)")
            return

        self._enumeration_busy = True

        def worker() -> None:
            try:
                try:
                    found = ZeroconfServiceTypes.find(zc=None, timeout=self._enumeration_timeout_s)
                except Exception:
                    self._logger.exception("mDNS service type enumeration failed")
                    return

                additions: list[str] = []
                seen: set[str] = set()
                for raw in found:
                    if not isinstance(raw, str) or not raw.strip():
                        continue
                    normalized = self._normalize_service_type(raw)
                    if normalized in seen:
                        continue
                    meta = "_services._dns-sd._udp" in normalized
                    # Avoid browsing the enumerator itself as a generic service catalogue.
                    if meta:
                        continue
                    seen.add(normalized)
                    additions.append(normalized)

                self._enqueue_browsers_for_types(additions)
                self._logger.info("mDNS enumeration found %d service type(s)", len(additions))
            finally:
                self._enumeration_busy = False

        threading.Thread(target=worker, name="NetNeighbor-mdns-enumerate", daemon=True).start()

    def _schedule_enumeration_timer(self) -> None:
        if self._enumeration_timer is not None:
            return

        def on_timer_fire() -> None:
            self._enumeration_timer = None
            if not self._running or self._zeroconf is None:
                return

            def kick() -> None:
                self._kick_service_type_enumeration()

            self._schedule_main(kick)
            if self._running:
                self._schedule_enumeration_timer()

        timer = threading.Timer(float(self._enumeration_interval_s), on_timer_fire)
        timer.daemon = True
        self._enumeration_timer = timer
        timer.start()

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
            if isinstance(parsed, dict):
                overlay = optional_user_json(USER_DEVICE_TYPES_JSON)
                if overlay is not None:
                    parsed = merge_device_types_trees(parsed, overlay)
                    self._logger.info("Merged device_types.json with user overlay %s", USER_DEVICE_TYPES_JSON)
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
        # Service is re-announcing — cancel any pending grace-remove immediately so
        # the UI never sees the record disappear if re-announcement arrives in time.
        self._cancel_grace_remove((service_type, name))
        try:
            info = zc.get_service_info(service_type, name, timeout=self._service_info_timeout_ms)
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
        previous_endpoint = self._host_last_endpoint.get(host_key)
        current_endpoint = (str(aggregate.get("ip", "0.0.0.0")), int(aggregate.get("port", 0) or 0))
        if previous_endpoint and previous_endpoint != current_endpoint:
            stale_payload = dict(aggregate)
            stale_payload["ip"] = previous_endpoint[0]
            stale_payload["port"] = previous_endpoint[1]
            stale_payload["online"] = False
            self._emit("device", stale_payload)
        self._host_last_endpoint[host_key] = current_endpoint
        self._emit("device", aggregate)

    def _on_service_remove(self, service_type: str, name: str) -> None:
        if not self._running:
            return
        key = (service_type, name)
        host_key = self._service_host_keys.get(key, "")
        if host_key:
            # Check if host is still alive via other services (excluding this key).
            still_alive = any(
                v == host_key for k, v in self._service_host_keys.items() if k != key
            )
            if still_alive:
                # Host is still up — delay the removal to absorb TTL re-announcement
                # jitter.  If add_service fires within the grace window the timer is
                # cancelled and the UI never sees the record disappear.
                self._schedule_grace_remove(key, host_key)
                return
        # Host going fully offline (or no known host) — apply immediately.
        self._cancel_grace_remove(key)
        self._apply_remove(key)

    def _schedule_grace_remove(self, key: tuple[str, str], _host_key: str) -> None:
        self._cancel_grace_remove(key)

        def apply_remove() -> None:
            self._pending_remove_timers.pop(key, None)
            if not self._running:
                return
            self._apply_remove(key)

        def _fire() -> None:
            self._schedule_main(apply_remove)

        timer = threading.Timer(float(_SERVICE_REMOVE_GRACE_S), _fire)
        timer.daemon = True
        self._pending_remove_timers[key] = timer
        timer.start()
        self._logger.debug(
            "mDNS grace remove scheduled for %s %s (host still alive, %ds delay)",
            key[0], key[1], _SERVICE_REMOVE_GRACE_S,
        )

    def _cancel_grace_remove(self, key: tuple[str, str]) -> None:
        timer = self._pending_remove_timers.pop(key, None)
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _apply_remove(self, key: tuple[str, str]) -> None:
        """Commit a service removal after the optional grace delay has elapsed."""
        self._seen_by_service.pop(key, None)
        host_key = self._service_host_keys.pop(key, "")
        if host_key:
            still_online = any(value == host_key for value in self._service_host_keys.values())
            if still_online:
                self._emit("device", self._aggregate_payload_for_host(host_key, online=True))
                return
            self._host_last_endpoint.pop(host_key, None)
            payload = self._aggregate_payload_for_host(host_key, online=False)
            self._emit("device", payload)
            return
        service_type, name = key
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
        txt, txt_records = self._txt_from_service_info(info)
        instance_label = name.split("._", 1)[0].strip() if name else ""

        display_name = self._infer_display_name(name, txt, self._to_text(server))
        type_name = str(mapping.get("type", "unknown")).strip().lower() or "unknown"
        type_name = self._infer_type_from_context(type_name, service_key, display_name, txt)
        category = self._category_for_type(
            type_name,
            str(mapping.get("category", _("Unknown Devices"))) or _("Unknown Devices"),
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
                    "instance": instance_label,
                    "txt": txt,
                    "txt_records": txt_records,
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
            str(mapping.get("category", _("Unknown Devices"))) or _("Unknown Devices"),
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
        if "_printer._tcp" in service_key or "_ipp._tcp" in service_key or "_ipps._tcp" in service_key:
            return "networkprinter"
        if "_airplay._tcp" in service_key or "_raop._tcp" in service_key or "_companion-link._tcp" in service_key:
            return "smartspeaker"
        if "_ftp._tcp" in service_key or "_afpovertcp._tcp" in service_key or "_webdav._tcp" in service_key:
            # NAS heuristic refines Synology/QNAP in TXT / name.
            return "computer"
        if "_scanner" in service_key or "_uscan" in service_key or "_uscans." in service_key:
            return "scanner"
        if "_http._tcp" in service_key or "_https._tcp" in service_key:
            return "http"
        return "unknown"

    def _infer_type_from_context(
        self,
        mapped_type: str,
        service_key: str,
        display_name: str,
        txt: dict[str, str],
        merged_services_line: str = "",
    ) -> str:
        base = mapped_type if mapped_type and mapped_type != "unknown" else self._infer_type_from_service(service_key)
        # Collapse printer variants into one device type class.
        if base == "printer":
            base = "networkprinter"

        haystack_parts = [service_key, display_name, merged_services_line]
        haystack_parts.extend([f"{k}={v}" for k, v in txt.items()])
        haystack = " ".join([part.lower() for part in haystack_parts if isinstance(part, str)])

        if "fluidnc" in haystack:
            return "cnc"
        if "laserjet" in haystack:
            return "networkprinter"
        if "synology" in haystack or "qnap" in haystack or " nas " in f" {haystack} ":
            return "nas"
        if not self._rules_enabled:
            return base
        return evaluate_type_rules(haystack, base, self._mdns_rules)

    def _aggregate_type_rank(self, device_type: str) -> int:
        """Higher = stronger signal for host-level aggregate type (icon + category)."""
        t = (device_type or "unknown").strip().lower()
        return {
            "unknown": 0,
            "http": 12,
            "https": 12,
            "computer": 20,
            "esp32": 22,
            "nas": 38,
            "mediaserver": 34,
            "smartspeaker": 40,
            "networkprinter": 55,
            "multifunction_printer": 56,
            "printer": 55,
            "scanner": 52,
        }.get(t, 8)

    def _best_aggregate_mdns_type(self, seed: str, merged_services: list) -> str:
        best = (seed or "unknown").strip().lower()
        best_r = self._aggregate_type_rank(best)
        for svc in merged_services:
            if not isinstance(svc, dict):
                continue
            svc_key = str(svc.get("service", "")).lower()
            cand = self._infer_type_from_service(svc_key)
            cr = self._aggregate_type_rank(cand)
            if cr > best_r:
                best = cand
                best_r = cr
        return best

    def _merged_services_imply_multifunction_printer(self, merged_services: list) -> bool:
        """Printer-class DNS-SD plus scanner / eSCL on the same host → multifunction."""
        parts: list[str] = []
        for svc in merged_services:
            if not isinstance(svc, dict):
                continue
            parts.append(str(svc.get("service", "")).lower())
        line = " ".join(parts)
        printer = any(
            x in line
            for x in ("_ipp._tcp", "_ipps._tcp", "_printer._tcp", "_pdl-datastream._tcp")
        )
        scanner = any(
            tok in line
            for tok in (
                "_uscan._tcp",
                "_uscans._tcp",
                "_scanner._tcp",
                "_scan._tcp",
                "_escl._tcp",
            )
        ) or ("scanner" in line and "._tcp" in line)
        return bool(printer and scanner)

    def _dns_target_label_from_server(self, server: str) -> str:
        """SRV / PTR target host (often ``thing.local``) without ``.local`` — good NAS / printer labels."""
        s = self._to_text(server).strip().strip(".")
        if not s:
            return ""
        low = s.lower()
        if low.endswith(".local"):
            s = s[:-6].strip(".")
        elif low.endswith(".local."):
            s = s[:-7].strip(".")
        return s.replace("-", " ").strip() if s else ""

    def _txt_product_value(self, txt: dict[str, str]) -> str:
        """Non-empty ``product`` TXT field if present (case-insensitive key)."""
        if not isinstance(txt, dict):
            return ""
        for raw_k, raw_v in txt.items():
            if not isinstance(raw_k, str) or raw_k.lower() != "product":
                continue
            if isinstance(raw_v, str) and raw_v.strip():
                return raw_v.strip()
        return ""

    def _infer_display_name(self, service_instance: str, txt: dict[str, str], server: str = "") -> str:
        # Order: friendly TXT first; Google Cast uses ``fn``. DNS target (without .local) before hardware
        # ``model`` so NAS / printers show hostname when TXT advertises a bare model string.
        for key in ("name", "fn", "friendlyname", "friendly_name", "device"):
            value = txt.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        dns_label = self._dns_target_label_from_server(server)
        if dns_label:
            prod = self._txt_product_value(txt)
            if prod:
                return f"{dns_label} ({prod})"
            return dns_label
        for key in ("model", "mdl", "md", "product"):
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

    def _unpack_txt_bytes(self, text: bytes) -> list[tuple[str, str]]:
        """Parse concatenated RFC 6763 TXT blobs; preserves order and duplicate keys.

        zeroconf's internal unpack keeps only the first occurrence per key — some devices expose
        multiple strings or duplicated keys across the TXT compound.
        """
        if not text:
            return []
        pairs: list[tuple[str, str]] = []
        index = 0
        end = len(text)
        while index < end:
            length = text[index]
            index += 1
            if length == 0:
                continue
            segment = text[index : index + length]
            index += length
            split = segment.split(b"=", 1)
            key = self._to_text(split[0]).strip()
            if not key and len(split) < 2:
                continue
            value = self._to_text(split[1]).strip() if len(split) > 1 else ""
            pairs.append((key, value))
        return pairs

    def _txt_from_service_info(self, info) -> tuple[dict[str, str], list[tuple[str, str]]]:
        """TXT as dict (last wins duplicate keys for heuristics) + ordered record list."""

        pairs: list[tuple[str, str]] = []
        raw_text = getattr(info, "text", None)
        if isinstance(raw_text, (bytes, bytearray)) and len(raw_text) > 0:
            pairs = self._unpack_txt_bytes(bytes(raw_text))
        if not pairs:
            props = getattr(info, "properties", None)
            if isinstance(props, dict):
                for raw_key, raw_value in props.items():
                    key = self._to_text(raw_key).strip()
                    if not key:
                        continue
                    val = "" if raw_value is None else self._to_text(raw_value).strip()
                    pairs.append((key, val))
        merged: dict[str, str] = {}
        for key, val in pairs:
            if key:
                merged[key] = val
        return merged, pairs

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
                "category": _("Unknown Devices"),
                "source": "mdns",
                "url": None,
                "metadata": {"service": "_mdns._udp.local.", "txt": {}, "services": []},
                "online": bool(online),
                "icon": self._icon_for_type("unknown"),
            }

        def _score(entry: dict) -> tuple[int, int]:
            metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
            service_text = str(metadata.get("service", "")).lower()
            # Prefer print-related rows over bare _http so aggregate type/icon stay printer-like
            # (avoids http → GTK "browser" flash before IPP TXT / device icon URL arrives).
            if any(
                x in service_text
                for x in ("_ipp._tcp", "_ipps._tcp", "_printer._tcp", "_pdl-datastream._tcp")
            ):
                return (0, 0)
            # Prefer playback / casting rows over bare _http (room TXT + artwork URLs).
            if any(
                x in service_text
                for x in (
                    "_airplay._tcp",
                    "_raop._tcp",
                    "_companion-link._tcp",
                    "_sonos._tcp",
                    "_spotify-connect._tcp",
                    "_googlecast._tcp",
                )
            ):
                return (1, 0)
            if "_http._tcp" in service_text or "_https._tcp" in service_text:
                return (2, 0)
            if "_esp3d._tcp" in service_text:
                return (3, 0)
            return (4, int(entry.get("port", 0) or 0))

        representative = sorted(entries, key=_score)[0]
        rep_metadata = representative.get("metadata") if isinstance(representative.get("metadata"), dict) else {}
        merged_services: list[dict] = []
        combined_txt: dict[str, str] = {}
        for entry in entries:
            metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
            entry_txt = metadata.get("txt")
            if isinstance(entry_txt, dict):
                combined_txt.update(entry_txt)
            services_list = metadata.get("services") if isinstance(metadata.get("services"), list) else []
            for service in services_list:
                if not isinstance(service, dict):
                    continue
                normalized_service = str(service.get("service", "")).strip()
                normalized_port = int(service.get("port", 0) or 0)
                inst_norm = str(service.get("instance", "")).strip()
                duplicate = any(
                    str(existing.get("service", "")).strip() == normalized_service
                    and int(existing.get("port", 0) or 0) == normalized_port
                    and str(existing.get("instance", "")).strip() == inst_norm
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
        if combined_txt:
            merged_metadata["txt"] = combined_txt

        seed_type = str(representative.get("type", "unknown")).strip().lower()
        services_line_for_infer = " ".join(
            str(s.get("service", "")).lower() for s in merged_services if isinstance(s, dict)
        )
        agg_type = self._infer_type_from_context(
            seed_type,
            str(rep_metadata.get("service", "")).lower(),
            str(representative.get("name", "mDNS Device")),
            combined_txt,
            services_line_for_infer,
        )
        agg_type = self._best_aggregate_mdns_type(agg_type, merged_services)
        if self._merged_services_imply_multifunction_printer(merged_services):
            agg_type = "multifunction_printer"
        icon_out = representative.get("icon")
        if not isinstance(icon_out, str) or not icon_out.strip():
            icon_out = self._icon_for_type(agg_type)
        elif self._aggregate_type_rank(agg_type) > self._aggregate_type_rank(seed_type):
            icon_out = self._icon_for_type(agg_type)
        category = self._category_for_type(agg_type, representative.get("category", _("Unknown Devices")))

        rep_svc = str(rep_metadata.get("service", ""))
        self._logger.debug(
            "mDNS aggregate host=%s ip=%s rows=%d rep_service=%r rep_port=%s seed_type=%s agg_type=%s icon=%s "
            "merged_txt_keys=%d merged_services=%d",
            host_key,
            ip,
            len(entries),
            rep_svc,
            representative.get("port"),
            seed_type,
            agg_type,
            icon_out,
            len(combined_txt),
            len(merged_services),
        )

        return {
            "name": representative.get("name", "mDNS Device"),
            "ip": ip,
            "port": int(representative.get("port", 0) or 0),
            "type": agg_type,
            "category": category,
            "source": "mdns",
            "url": url,
            "metadata": merged_metadata,
            "online": bool(online),
            "icon": icon_out,
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
            "router": _("Routers & Gateways"),
            "mediaserver": _("Media Servers"),
            "scanner": _("Printers"),
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
            "esp32": _("ESP3D Devices"),
            "http": _("Unknown Devices"),
            "unknown": _("Unknown Devices"),
        }.get(device_type, fallback or _("Unknown Devices"))

    def _icon_for_type(self, device_type: str) -> str | None:
        return {
            "router": "router.png",
            "mediaserver": "mediaserver.png",
            "scanner": "printer.png",
            "printer": "printer.png",
            "networkprinter": "printer.png",
            "multifunction_printer": "printer.png",
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
