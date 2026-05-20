# File manager.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Orchestrates all protocol providers and keeps a simple device cache."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import ipaddress
import logging
import threading
from urllib.parse import urljoin, urlparse, urlunparse
from typing import Literal

from discovery.base import BaseDiscovery
from discovery.mdns import MDNSDiscovery
from discovery.ssdp import SSDPDiscovery
from discovery.netbios import NetbiosDiscovery
from discovery.wsdd_client import WsddSocketDiscovery
from discovery.wsd import WSDiscovery, is_synthetic_wsd_display_name
from model.device import Device
from utils.discovery_cache import CACHE_MAX_AGE_HOURS, load_discovery_cache
from utils.discovery_config import normalize_information_precedence_list
from utils.device_bundles import normalize_mac_for_bundle_merge
from utils.discovery_identity import (
    normalize_monitored_name,
    normalize_monitored_uid,
    upnp_identity_from_udn,
    upnp_identity_from_usn,
    uuid_urn_if_present,
)
from utils.neighbor_mac import lookup_mac_from_neighbor_cache
from utils.location_label import is_plausible_room_location
from utils.scheduling import ScheduleMainFn

AnticipatoryDescriptorSource = Literal["ssdp_profile_cache", "mdns_txt"]

PresenceTransitionKind = Literal["online", "offline"]
PresenceTransitionHook = Callable[[Device, PresenceTransitionKind], None]
_IDENTITY_HOLD_SECONDS = 3.0
# Coalesce burst ``_notify()`` calls (mDNS / merge can fire many times per 100 ms window).
_NOTIFY_DEBOUNCE_SECONDS = 0.08


def _descriptor_url_with_ip(descriptor_template: str, ip_s: str) -> str | None:
    """Rebuild SSDP LOCATION-style URL with ``ip_s`` as host (same path/port/query as template)."""
    t = (descriptor_template or "").strip()
    if not t:
        return None
    try:
        addr = ipaddress.ip_address(ip_s.strip())
    except ValueError:
        return None
    p = urlparse(t)
    if not p.scheme or not p.netloc:
        return None
    port = p.port
    if isinstance(addr, ipaddress.IPv6Address):
        host = f"[{addr.compressed}]"
    else:
        host = addr.compressed
    netloc = f"{host}:{port}" if port is not None else host
    return urlunparse((p.scheme, netloc, p.path or "/", p.params, p.query, p.fragment))


def _http_base_url_for_descriptor(ip_s: str, port: int, *, https: bool = False) -> str | None:
    """Build ``http(s)://host[:port]/`` for resolving relative descriptor paths from mDNS TXT."""
    try:
        addr = ipaddress.ip_address(ip_s.strip())
    except ValueError:
        return None
    scheme = "https" if https else "http"
    default_port = 443 if https else 80
    if isinstance(addr, ipaddress.IPv6Address):
        h = f"[{addr.compressed}]"
    else:
        h = addr.compressed
    p = int(port) if port else 0
    if p > 0 and p != default_port:
        netloc = f"{h}:{p}"
    else:
        netloc = h
    return urlunparse((scheme, netloc, "/", "", "", ""))


def _normalize_mdns_relative_descriptor_path(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if s.startswith(("http://", "https://")):
        return s
    low = s.lower()
    if ".xml" not in low:
        return None
    if s.startswith("/"):
        return s
    # Some TXT records use ``description.xml`` without a leading slash.
    return "/" + s.lstrip("/")


def _join_descriptor_base_and_path(base_url: str, absolute_path: str) -> str | None:
    """Like URL concatenation but preserves non-default port (``urljoin`` strips it for absolute paths)."""
    path = absolute_path if absolute_path.startswith("/") else f"/{absolute_path}"
    try:
        p = urlparse(base_url)
    except ValueError:
        return None
    if not p.scheme or not p.netloc:
        return None
    return urlunparse((p.scheme, p.netloc, path, "", "", ""))


_VALID_COMMAND_SCHEMES = frozenset(("http", "https", "smb", "ftp", "ssh", "sftp", "telnet", "custom"))
_VALID_COMMAND_MODES = frozenset(("override", "additional"))


def _normalize_command_list(raw: object) -> list[dict]:
    """Validate and normalize a list of per-device command dicts."""
    if not isinstance(raw, list):
        return []
    result = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        scheme = str(c.get("scheme", "")).strip().lower()
        if scheme not in _VALID_COMMAND_SCHEMES:
            continue
        mode = str(c.get("mode", "override")).strip().lower()
        if mode not in _VALID_COMMAND_MODES:
            mode = "override"
        result.append({
            "scheme": scheme,
            "ip": str(c.get("ip", "")).strip(),
            "port": int(c.get("port", 0) or 0),
            "mode": mode,
            "label": str(c.get("label", "")).strip(),
        })
    return result


class DiscoveryManager:
    def __init__(
        self,
        demo_mode: bool = False,
        enable_ssdp: bool = True,
        enable_mdns: bool = True,
        enable_ssdp_rules: bool = True,
        enable_mdns_rules: bool = True,
        ssdp_query_interval_seconds: int | None = None,
        ssdp_mx_seconds: int | None = None,
        ssdp_descriptor_http_min_interval_seconds: float | None = None,
        ssdp_msearch_directed_ips: list[str] | None = None,
        mdns_enumeration_timeout_seconds: float | None = None,
        mdns_enumeration_interval_seconds: int | None = None,
        mdns_service_info_timeout_ms: int | None = None,
        enable_wsd: bool = True,
        wsd_interval_seconds: float | None = None,
        wsd_timeout_seconds: float | None = None,
        enable_wsdd_socket: bool = False,
        wsdd_listen: str | None = None,
        wsdd_interval_seconds: float | None = None,
        wsdd_socket_timeout_seconds: float | None = None,
        wsdd_probe_each_poll: bool = True,
        enable_nmb: bool = True,
        nmb_interval_seconds: float | None = None,
        nmb_timeout_seconds: float | None = None,
        nmb_argv: list[str] | None = None,
        nmb_directed_ips: list[str] | None = None,
        protocol_merge_order: list[str] | None = None,
        information_precedence: list[str] | None = None,
        schedule_on_main_thread: ScheduleMainFn | None = None,
    ) -> None:
        self._logger = logging.getLogger(__name__)
        self._ssdp_logger = logging.getLogger(f"{__name__}.ssdp")
        self._mdns_logger = logging.getLogger(f"{__name__}.mdns")
        self._wsd_logger = logging.getLogger(f"{__name__}.wsd")
        self._wsdd_logger = logging.getLogger(f"{__name__}.wsdd")
        self._nmb_logger = logging.getLogger(f"{__name__}.nmb")
        self._protocol_merge_order: list[str] = (
            list(protocol_merge_order)
            if protocol_merge_order
            else ["ssdp", "wsd", "wsdd", "nmb", "mdns"]
        )
        self._information_precedence: list[str] = (
            list(information_precedence)
            if information_precedence
            else normalize_information_precedence_list(None)
        )
        self._schedule_on_main_thread: ScheduleMainFn = (
            schedule_on_main_thread if schedule_on_main_thread is not None else (lambda fn: fn())
        )
        self._logger.debug("merge.information_precedence (manager) = %s", self._information_precedence)
        protocols: list[BaseDiscovery] = []
        if enable_ssdp:
            protocols.append(
                SSDPDiscovery(
                    rules_enabled=enable_ssdp_rules,
                    query_interval_seconds=ssdp_query_interval_seconds,
                    mx_seconds=ssdp_mx_seconds,
                    descriptor_http_min_interval_seconds=ssdp_descriptor_http_min_interval_seconds,
                    msearch_directed_ips=ssdp_msearch_directed_ips,
                    ephemeral_msearch_probe_ips=self._ssdp_probe_ips_from_mdns_without_ssdp,
                )
            )
        if enable_mdns:
            protocols.append(
                MDNSDiscovery(
                    rules_enabled=enable_mdns_rules,
                    enumeration_timeout_seconds=mdns_enumeration_timeout_seconds,
                    enumeration_interval_seconds=mdns_enumeration_interval_seconds,
                    service_info_timeout_ms=mdns_service_info_timeout_ms,
                    schedule_on_main_thread=self._schedule_on_main_thread,
                )
            )
        if enable_wsd:
            protocols.append(
                WSDiscovery(
                    interval_seconds=wsd_interval_seconds,
                    timeout_seconds=wsd_timeout_seconds,
                )
            )
        if enable_wsdd_socket and (wsdd_listen or "").strip():
            protocols.append(
                WsddSocketDiscovery(
                    listen=(wsdd_listen or "").strip(),
                    interval_seconds=wsdd_interval_seconds,
                    socket_timeout_seconds=wsdd_socket_timeout_seconds,
                    probe_each_poll=wsdd_probe_each_poll,
                )
            )
        if enable_nmb:
            protocols.append(
                NetbiosDiscovery(
                    interval_seconds=nmb_interval_seconds,
                    timeout_seconds=nmb_timeout_seconds,
                    argv=nmb_argv,
                    directed_ips=nmb_directed_ips,
                )
            )
        self._protocols = protocols
        self._ssdp_discovery: SSDPDiscovery | None = None
        self._nmb_discovery: NetbiosDiscovery | None = None
        for _p in protocols:
            if isinstance(_p, SSDPDiscovery):
                self._ssdp_discovery = _p
            if isinstance(_p, NetbiosDiscovery):
                self._nmb_discovery = _p
        self._anticipatory_fetch_attempted: set[str] = set()
        self._anticipatory_reentrant: set[str] = set()
        self._devices: dict[str, Device] = {}
        self._arrival_sequence = 0
        self._listeners: list[Callable[[list[Device]], None]] = []
        self._notify_debounce_lock = threading.Lock()
        self._notify_debounce_timer: threading.Timer | None = None
        self._stopped: bool = False
        self._presence_hooks: list[PresenceTransitionHook] = []
        self._type_overrides: dict[str, str] = {}
        self._name_overrides: dict[str, str] = {}
        self._location_overrides: dict[str, str] = {}
        self._url_overrides: dict[str, str] = {}
        self._device_commands: dict[str, list[dict]] = {}  # key → [{scheme,ip,port,mode,label}]
        self._custom_command_overrides: dict[str, str] = {}  # key → command template
        self._field_mapping_rules: dict[str, dict[str, list[str]]] = {}
        self._monitored_overrides: dict[str, bool] = {}
        self._hidden_overrides: dict[str, bool] = {}
        self._last_seen_overrides: dict[str, str] = {}
        # One "online" notification per LAN host until it goes offline (SSDP + mDNS rows share IP).
        self._presence_announced_hosts: set[str] = set()
        self._identity_pending: dict[str, tuple[datetime, Device]] = {}
        self._ssdp_profile_cache_by_ip: dict[str, dict]
        self._ssdp_profile_cache_emit_rows: list[tuple[str, dict]]
        self._ssdp_profile_cache_by_ip, self._ssdp_profile_cache_emit_rows = (
            self._load_ssdp_profile_cache_by_ip()
        )
        self._demo_mode = demo_mode
        self._location_prefs_need_reapply = False
        self._location_prefs_dirty_callback: Callable[[], None] | None = None

        for protocol in self._protocols:
            protocol.set_callback(self._on_protocol_event)

    def set_location_prefs_dirty_callback(self, callback: Callable[[], None] | None) -> None:
        self._location_prefs_dirty_callback = callback

    def add_listener(self, callback: Callable[[list[Device]], None]) -> None:
        self._listeners.append(callback)
        callback(self.devices)

    def register_presence_transition_hook(self, hook: PresenceTransitionHook) -> None:
        """Notify when a device becomes reachable or unreachable (same identity row).

        Fires only on transitions: newly online (including back from offline), or offline from
        online. Silent for first-seen rows that are already offline (e.g. restored monitored
        ghosts).         Plugins should treat callbacks as potentially running on a discovery thread —
        marshal to the UI thread before touching GUI toolkit APIs.

        Reserved for future automation / plugins; core code does not register hooks today.
        """
        self._presence_hooks.append(hook)

    def unregister_presence_transition_hook(self, hook: PresenceTransitionHook) -> None:
        try:
            self._presence_hooks.remove(hook)
        except ValueError:
            pass

    @property
    def devices(self) -> list[Device]:
        return sorted(self._devices_snapshot(), key=lambda device: (device.category, device.name.lower()))

    def _devices_snapshot(self) -> list[Device]:
        """Copy of live rows — safe while discovery threads mutate ``_devices``."""
        return list(self._devices.values())

    def start(self) -> None:
        self._stopped = False
        self._logger.info("Starting discovery protocols: %s", [p.source for p in self._protocols])
        self._emit_cached_devices()
        self._start_tcp_probes_for_cached_devices()
        if not self._protocols:
            self._logger.warning("No discovery protocols enabled (check ~/.config/netneighbor/discovery.json)")
            return
        for protocol in self._protocols:
            protocol.start()

    def stop(self) -> None:
        self._stopped = True
        with self._notify_debounce_lock:
            if self._notify_debounce_timer is not None:
                self._notify_debounce_timer.cancel()
                self._notify_debounce_timer = None
        self._logger.info("Stopping discovery protocols")
        for protocol in self._protocols:
            protocol.stop()
        self._identity_pending.clear()
        self._presence_announced_hosts.clear()

    def refresh(self) -> None:
        self._logger.debug("Manual refresh requested")
        for protocol in self._protocols:
            protocol.refresh()

    def _ssdp_probe_ips_from_mdns_without_ssdp(self) -> list[str]:
        """IPv4 addresses with live mDNS rows but no SSDP row (unicast M-SEARCH fills NOTIFY gaps)."""
        ssdp_ips: set[str] = set()
        for d in self._devices.values():
            if d.source == "ssdp" and d.online:
                sip = str(d.ip).strip()
                if not sip:
                    continue
                try:
                    addr = ipaddress.ip_address(sip)
                except ValueError:
                    ssdp_ips.add(sip)
                    continue
                ssdp_ips.add(addr.compressed)
        seen: set[str] = set()
        out: list[str] = []
        for d in self._devices.values():
            if d.source != "mdns" or not d.online:
                continue
            sip = str(d.ip).strip()
            if not sip:
                continue
            try:
                addr = ipaddress.ip_address(sip)
            except ValueError:
                continue
            if not isinstance(addr, ipaddress.IPv4Address) or addr.is_loopback:
                continue
            canon = addr.compressed
            if canon in ssdp_ips:
                continue
            if canon not in seen:
                seen.add(canon)
                out.append(canon)
        return out

    def _device_event_logger(self, source: str) -> logging.Logger:
        if source == "ssdp":
            return self._ssdp_logger
        if source == "mdns":
            return self._mdns_logger
        if source == "wsd":
            return self._wsd_logger
        if source == "wsdd":
            return self._wsdd_logger
        if source == "nmb":
            return self._nmb_logger
        return self._logger

    def add_or_update_device(self, device: Device) -> None:
        sip = str(device.ip).strip()
        if sip in {"", "0.0.0.0"}:
            self._device_event_logger(device.source).debug(
                "Ignoring device with placeholder IP: source=%s name=%s port=%s online=%s",
                device.source,
                device.name,
                device.port,
                device.online,
            )
            return
        # Loopback addresses are always the local machine — never useful as
        # network neighbours.  Drop them unconditionally.
        try:
            if ipaddress.ip_address(sip).is_loopback:
                self._device_event_logger(device.source).debug(
                    "Ignoring loopback device: source=%s ip=%s name=%s",
                    device.source,
                    sip,
                    device.name,
                )
                return
        except ValueError:
            pass
        # WSD/WSDD devices without a resolvable MAC are suppressed: they cannot be
        # merged with the IPv4 entry for the same host and would appear as a duplicate.
        # Offline events are still processed so a previously stored device can go offline.
        if device.source in {"wsd", "wsdd"} and device.online:
            if not self._mac_for_device_identity(device):
                self._device_event_logger(device.source).debug(
                    "Suppressing WSD/WSDD device without MAC: ip=%s name=%s",
                    device.ip,
                    device.name,
                )
                return

        if self._should_hold_for_stable_identity(device):
            return

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

        existing_key = device.key
        existing = self._devices.get(existing_key)
        if existing is None and device.source == "ssdp":
            existing_key, existing = self._find_existing_ssdp_by_endpoint(device)

        self._hydrate_mdns_from_ssdp_profile_cache(device, existing)
        self._hydrate_ssdp_from_profile_cache(device)
        self._schedule_anticipatory_descriptor_fetch(device)
        self._apply_field_mapping_rules(device)
        self._apply_type_override(device)
        self._apply_name_override(device)
        self._apply_location_override(device)
        self._apply_url_override(device)
        self._apply_device_commands(device)
        self._apply_custom_command_override(device)
        self._apply_monitored_override(device)
        self._apply_hidden_override(device)

        prev_online: bool | None = existing.online if existing is not None else None

        if existing is not None:
            # Preserve user-follow choice across updates.
            device.monitored = existing.monitored
            device.hidden = existing.hidden
            if device.online is False and existing.last_seen:
                device.last_seen = existing.last_seen
            if device.source == "ssdp":
                self._ssdp_logger.debug(
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

        if not isinstance(device.metadata, dict):
            device.metadata = {}
        existing_arrival = None
        if isinstance(existing, Device) and isinstance(existing.metadata, dict):
            candidate = existing.metadata.get("_arrival_index")
            if isinstance(candidate, int):
                existing_arrival = candidate
        if existing_arrival is None:
            self._arrival_sequence += 1
            existing_arrival = self._arrival_sequence
        device.metadata["_arrival_index"] = existing_arrival
        self._supplement_missing_user_location(device, existing)
        self._devices[existing_key] = device
        self._maybe_queue_nmb_probe_for_synthetic_wsd(device)
        self._run_location_reapply_sweep()
        device_log = self._device_event_logger(device.source)
        device_log.info(
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
        device_log.debug(
            "Device %s: %s %s:%s [%s] online=%s category=%s",
            "updated" if existing else "added",
            device.source,
            device.ip,
            device.port,
            device.name,
            device.online,
            device.category,
        )
        uloc = device.metadata.get("user_location") if isinstance(device.metadata, dict) else None
        device_log.debug(
            "Device appearance: ip=%s port=%s type=%s icon=%s user_location=%r",
            device.ip,
            device.port,
            device.type,
            device.icon,
            uloc,
        )
        self._emit_presence_hooks_if_transition(device, prev_online, device.online)
        self._notify()

    def _maybe_queue_nmb_probe_for_synthetic_wsd(self, device: Device) -> None:
        """Queue ``nmblookup -A`` on the LAN IPv4 for synthetic WSD/wsdd labels.

        Broadcast browse often misses PCs; directed ``-A`` matches what you'd run manually.
        If discovery only has IPv6 (e.g. link-local), resolve IPv4 from kernel neighbor/MAC
        tables—same source as ``ip neigh``—then probe that address for the NetBIOS name.
        """
        nmb = self._nmb_discovery
        if nmb is None:
            return
        if (device.source or "").strip().lower() not in {"wsd", "wsdd"}:
            return
        if not device.online:
            return
        if (device.type or "").strip().lower() != "computer":
            return
        if not is_synthetic_wsd_display_name(device.name):
            return
        sip = str(device.ip).strip()
        base = sip.split("%", 1)[0].strip()
        if not base:
            return
        try:
            a = ipaddress.ip_address(base)
        except ValueError:
            return
        target_v4: str | None = None
        if isinstance(a, ipaddress.IPv4Address):
            if a.is_loopback or not a.is_private:
                return
            target_v4 = base
        elif isinstance(a, ipaddress.IPv6Address):
            from utils.details_payload import resolve_mac_for_device
            from utils.neighbor_mac import lookup_ipv4_for_mac

            mac = resolve_mac_for_device(device)
            if not mac:
                return
            target_v4 = lookup_ipv4_for_mac(mac)
            if not target_v4:
                return
        else:
            return
        nmb.suggest_directed_ip(target_v4)

    def _load_ssdp_profile_cache_by_ip(self) -> tuple[dict[str, dict], list[tuple[str, dict]]]:
        """Build SSDP profile cache indexes from disk.

        Returns ``(by_location_host, emit_rows)``:

        - ``by_location_host``: last row per LOCATION hostname — used for ``.get(ip)`` hydration
          (legacy behaviour).
        - ``emit_rows``: **every** cache entry that resolves a hostname, in iteration order — used
          to pre-populate the device store at startup.  Multiple UPnP rows often share the same
          LOCATION host (e.g. coordinator URL); indexing only by host would drop the others and the
          UI would show a single device until live SSDP re-fills, with apparent IP ``replacement``.
        """
        by_host: dict[str, dict] = {}
        emit_rows: list[tuple[str, dict]] = []
        cache_blob = load_discovery_cache()
        raw = cache_blob.get("ssdp_profile_cache")
        if not isinstance(raw, dict):
            return by_host, emit_rows
        entries = raw.get("entries")
        if not isinstance(entries, dict):
            return by_host, emit_rows
        for _key, row in entries.items():
            if not isinstance(row, dict):
                continue
            candidate_urls: list[str] = []
            ssdp_loc = row.get("ssdp_location")
            if isinstance(ssdp_loc, str) and ssdp_loc.strip():
                candidate_urls.append(ssdp_loc.strip())
            for u in (row.get("url"), (row.get("xml_fields") or {}).get("presentationURL")):
                if isinstance(u, str) and u.strip():
                    candidate_urls.append(u.strip())
            # Fallback from raw xml LOCATION URL if present in row.
            if not candidate_urls:
                raw_xml = row.get("raw_xml")
                if isinstance(raw_xml, str) and "URLBase>" in raw_xml:
                    start = raw_xml.find("URLBase>")
                    end = raw_xml.find("</URLBase>")
                    if start >= 0 and end > start:
                        candidate_urls.append(raw_xml[start + 8 : end].strip())
            host = ""
            for u in candidate_urls:
                try:
                    p = urlparse(u)
                except ValueError:
                    continue
                if p.hostname:
                    host = str(p.hostname).strip()
                    break
            if not host:
                continue
            row_copy = dict(row)
            emit_rows.append((host, row_copy))
            by_host[host] = row_copy
        return by_host, emit_rows

    def _emit_cached_devices(self) -> None:
        """Pre-populate the device store from the SSDP profile disk cache before protocols start.

        Iterates the cached SSDP profiles and emits each as a synthetic ``ssdp`` device so that
        previously-seen devices appear in the UI immediately at startup — without waiting 5-15 s
        for live discovery to respond.

        Cache entries older than 24 h are skipped (same TTL as the cache GC).  Once live
        protocol events arrive they overwrite these entries: a live SSDP row shares the same
        Device key (``ssdp:udn:…`` when a UDN is present, else ``ssdp:{ip}:{port}``) and simply
        replaces the pre-populated entry via the normal ``add_or_update_device`` pipeline.
        """
        now = datetime.now(timezone.utc)
        _cache_max_age_s = CACHE_MAX_AGE_HOURS * 3600
        emitted = 0
        for sip, row in self._ssdp_profile_cache_emit_rows:
            if not isinstance(row, dict):
                continue
            # TTL guard — skip entries not updated within the retention window.
            updated_raw = row.get("updated_at")
            updated_dt: datetime | None = None
            if isinstance(updated_raw, str):
                try:
                    updated_dt = datetime.fromisoformat(updated_raw)
                    if updated_dt.tzinfo is None:
                        updated_dt = updated_dt.replace(tzinfo=timezone.utc)
                    if (now - updated_dt).total_seconds() > _cache_max_age_s:
                        self._logger.debug("_emit_cached_devices: skipping stale entry ip=%s updated_at=%s", sip, updated_raw)
                        continue
                except ValueError:
                    pass  # malformed timestamp → include the entry rather than skip it
            name = self._ssdp_profile_display_name(row)
            if not name:
                name = f"Device ({sip})"
            device_type = row.get("type") or "unknown"
            if not isinstance(device_type, str):
                device_type = "unknown"
            device_category = row.get("category") or self._category_for_type(device_type)
            if not isinstance(device_category, str):
                device_category = self._category_for_type(device_type)
            # Derive port from ssdp_location URL (e.g. "http://192.168.1.103:8008/…").
            port = 0
            ssdp_loc = row.get("ssdp_location")
            if isinstance(ssdp_loc, str) and ssdp_loc.strip():
                try:
                    parsed = urlparse(ssdp_loc.strip())
                    if parsed.port:
                        port = parsed.port
                except Exception:
                    pass
            # Build metadata mirrors a lean SSDP device event so hydration / details work.
            metadata: dict = {"from_ssdp_cache": True}
            xml_fields = row.get("xml_fields")
            if isinstance(xml_fields, dict):
                metadata["xml_fields"] = dict(xml_fields)
            raw_xml = row.get("raw_xml")
            if isinstance(raw_xml, str) and raw_xml.strip():
                metadata["xml"] = raw_xml
            if isinstance(ssdp_loc, str) and ssdp_loc.strip():
                metadata["location"] = ssdp_loc.strip()
            usn_row = row.get("usn")
            if isinstance(usn_row, str) and usn_row.strip():
                metadata["usn"] = usn_row.strip()
            url = row.get("url")
            device = Device(
                name=name,
                ip=sip,
                port=port,
                type=device_type,
                category=device_category,
                source="ssdp",
                url=url if isinstance(url, str) and url.strip() else None,
                metadata=metadata,
                last_seen=updated_dt if updated_dt is not None else datetime.now(timezone.utc),
                online=True,
            )
            self.add_or_update_device(device)
            emitted += 1
        self._logger.info(
            "_emit_cached_devices: pre-populated %d device(s) from %d SSDP profile cache row(s)",
            emitted,
            len(self._ssdp_profile_cache_emit_rows),
        )

    def _start_tcp_probes_for_cached_devices(self) -> None:
        """Background TCP probe to quickly validate cached devices at startup.

        Devices that do not respond within 1.5 s are marked offline before live protocol
        discovery completes, giving the UI a clean picture within ~5 s of startup.
        Only devices with a usable port (>0, non-loopback, non-link-local) are probed.
        If a live protocol has already confirmed a device online since the probe started,
        the TCP result is discarded to avoid a false offline flip.
        """
        from utils.tcp_probe import probe_devices_background

        targets: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()
        for d in list(self._devices.values()):
            if not d.online:
                continue
            ip = str(d.ip).strip()
            port = int(d.port or 0)
            if not ip or port <= 0:
                continue
            try:
                addr = ipaddress.ip_address(ip)
                if addr.is_loopback:
                    continue
                if isinstance(addr, ipaddress.IPv6Address) and addr.is_link_local:
                    continue
            except ValueError:
                continue
            key = (ip, port)
            if key not in seen:
                seen.add(key)
                targets.append(key)

        if not targets:
            return

        self._logger.info("TCP probe: validating %d cached endpoint(s)", len(targets))
        probe_start = datetime.now(timezone.utc)

        def _on_result(ip: str, port: int, reachable: bool) -> None:
            changed = False
            now = datetime.now(timezone.utc)
            for d in list(self._devices.values()):
                sip = str(d.ip).strip()
                dport = int(d.port or 0)
                if sip != ip or dport != port:
                    continue
                if reachable:
                    if d.online:
                        d.last_seen = now
                        changed = True
                elif d.online and not (d.last_seen and d.last_seen > probe_start):
                    # Only mark offline if no protocol has confirmed online since probe start.
                    d.online = False
                    changed = True
            if changed:
                if reachable:
                    self._logger.debug("TCP probe: %s:%s reachable", ip, port)
                else:
                    self._logger.info("TCP probe: %s:%s unreachable → offline", ip, port)
                self._notify()

        probe_devices_background(targets, _on_result, timeout_s=1.5, max_workers=8)

    def _ssdp_profile_display_name(self, row: dict) -> str:
        """Best-effort human label from persisted SSDP profile (disk cache)."""
        n = row.get("name")
        if isinstance(n, str) and n.strip():
            return n.strip()
        xf = row.get("xml_fields") if isinstance(row.get("xml_fields"), dict) else {}
        for key in ("friendlyName", "displayName"):
            value = xf.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _hydrate_mdns_from_ssdp_profile_cache(self, device: Device, prior_row: Device | None = None) -> None:
        """Merge SSDP disk-cache hints onto an mDNS row.

        Precedence among inputs (see ``merge.information_precedence`` in ``~/.config/netneighbor/discovery.json``):
        user prefs override SSDP live; SSDP live overrides this disk cache; this cache overrides plain mDNS
        naming/type inference.

        **Rule 0** — user name / location prefs override everything (see
        :meth:`_has_effective_name_override`, :meth:`_apply_name_override`).
        **Rule 1** — persisted SSDP ``friendlyName`` / profile ``name`` overrides mDNS naming unless rule 0 applies.
        """
        if device.source != "mdns":
            return
        sip = str(device.ip).strip()
        if not sip:
            return
        row = self._ssdp_profile_cache_by_ip.get(sip)
        if not isinstance(row, dict):
            return
        if not isinstance(device.metadata, dict):
            device.metadata = {}
        xml_fields = row.get("xml_fields")
        if isinstance(xml_fields, dict):
            md_xml = device.metadata.get("xml_fields")
            if not isinstance(md_xml, dict):
                md_xml = {}
            for key, value in xml_fields.items():
                if key not in md_xml and value:
                    md_xml[key] = value
            if md_xml:
                device.metadata["xml_fields"] = md_xml
        # Also carry over raw XML and SSDP location so the "Device data" tab
        # appears in details even when there is no live SSDP device.
        if not device.metadata.get("xml"):
            raw_xml = row.get("raw_xml")
            if isinstance(raw_xml, str) and raw_xml.strip():
                device.metadata["xml"] = raw_xml
        if not device.metadata.get("location"):
            ssdp_loc = row.get("ssdp_location")
            if isinstance(ssdp_loc, str) and ssdp_loc.strip():
                device.metadata["location"] = ssdp_loc
        cached_display = self._ssdp_profile_display_name(row)
        if cached_display and not self._has_effective_name_override(device, prior_row):
            # Rule 1 vs mDNS; skipped when rule 0 applies (see _has_effective_name_override).
            device.name = cached_display
        self._apply_ssdp_cache_type_to_mdns(device, row)

    def _hydrate_ssdp_from_profile_cache(self, device: Device) -> None:
        """Pre-populate empty xml_fields on a live SSDP device from the disk profile cache.

        When the live XML fetch has not yet completed (or previously failed), the
        cached xml_fields from a prior successful fetch are used as an immediate
        starting point so the details panel is never blank.  The live fetch result
        will overwrite these values once it arrives (higher-precedence merge path).
        """
        if device.source != "ssdp":
            return
        sip = str(device.ip).strip()
        if not sip:
            return
        row = self._ssdp_profile_cache_by_ip.get(sip)
        if not isinstance(row, dict):
            return
        if not isinstance(device.metadata, dict):
            device.metadata = {}
        cached_xml = row.get("xml_fields")
        if not isinstance(cached_xml, dict) or not cached_xml:
            return
        md_xml = device.metadata.get("xml_fields")
        if not isinstance(md_xml, dict):
            md_xml = {}
        for key, value in cached_xml.items():
            if key not in md_xml and value:
                md_xml[key] = value
        if md_xml:
            device.metadata["xml_fields"] = md_xml
        if not device.metadata.get("xml"):
            raw_xml = row.get("raw_xml")
            if isinstance(raw_xml, str) and raw_xml.strip():
                device.metadata["xml"] = raw_xml
        if not device.metadata.get("location"):
            ssdp_loc = row.get("ssdp_location")
            if isinstance(ssdp_loc, str) and ssdp_loc.strip():
                device.metadata["location"] = ssdp_loc

    def _apply_ssdp_cache_type_to_mdns(self, device: Device, row: dict) -> None:
        """Use SSDP profile-cache type hints after merging ``xml_fields`` (not only when mDNS type is unknown).

        Samba ``_smb._tcp`` is inferred as ``computer`` before UUID/MAC may arrive; disk cache may already
        hold ``modelType: NAS`` or a richer ``type`` from a prior SSDP observation.
        """
        md = device.metadata if isinstance(device.metadata, dict) else {}
        xf = md.get("xml_fields") if isinstance(md.get("xml_fields"), dict) else {}
        mt = (xf.get("modelType") or "").strip().lower()
        if mt == "nas":
            device.type = "nas"
            device.category = self._category_for_type("nas")
            return
        cached = row.get("type")
        if not isinstance(cached, str) or not cached.strip():
            return
        ct = cached.strip().lower()
        cur = str(device.type or "").strip().lower()
        if cur == "unknown":
            device.type = ct
            device.category = self._category_for_type(ct)
            return
        weak = {"unknown", "http", "https", "computer"}
        if cur in weak and self._cross_protocol_type_rank(ct) > self._cross_protocol_type_rank(cur):
            device.type = ct
            device.category = self._category_for_type(ct)

    def _mdns_web_endpoint(self, md: dict, device: Device) -> tuple[int, bool]:
        """HTTP(S) port from merged mDNS ``_http._tcp`` / ``_https._tcp`` rows."""
        services = md.get("services") if isinstance(md.get("services"), list) else []
        https_p = 0
        http_p = 0
        for svc in services:
            if not isinstance(svc, dict):
                continue
            name = str(svc.get("service", "")).lower()
            port = int(svc.get("port", 0) or 0)
            if port <= 0:
                continue
            if "_https._tcp" in name:
                https_p = https_p or port
            if "_http._tcp" in name:
                http_p = http_p or port
        if https_p:
            return (https_p, True)
        if http_p:
            return (http_p, False)
        dp = int(device.port or 0)
        if dp > 0:
            return (dp, False)
        return (80, False)

    def _collect_mdns_descriptor_candidates(self, md: dict, ip: str, fallback_port: int, fallback_https: bool) -> list[tuple[int, str]]:
        scored: dict[str, int] = {}

        def push(url: str) -> None:
            u = (url or "").strip()
            if not u:
                return
            q = self._ssdp_location_quality_score(u)
            if q < 2:
                return
            prev = scored.get(u)
            if prev is None or q > prev:
                scored[u] = q

        def push_relative(path_fragment: str, port: int, https: bool) -> None:
            norm = _normalize_mdns_relative_descriptor_path(path_fragment)
            if not norm:
                return
            if norm.startswith(("http://", "https://")):
                push(norm)
                return
            base = _http_base_url_for_descriptor(ip, port, https=https)
            if not base:
                return
            full = _join_descriptor_base_and_path(base, norm)
            if full:
                push(full)

        agg_port, agg_https = int(fallback_port or 0), fallback_https
        if agg_port <= 0:
            agg_port = 80

        combined = md.get("txt") if isinstance(md.get("txt"), dict) else {}
        if isinstance(combined, dict):
            for _key, val in combined.items():
                if not isinstance(val, str):
                    continue
                v = val.strip()
                if v.startswith(("http://", "https://")):
                    push(v)
            for pk in ("path", "rp", "description_path", "descriptor_path", "device_description"):
                raw = combined.get(pk)
                if isinstance(raw, str) and raw.strip():
                    push_relative(raw, agg_port, agg_https)
            for _key, val in combined.items():
                if not isinstance(val, str):
                    continue
                v = val.strip()
                if ".xml" not in v.lower():
                    continue
                if v.startswith(("http://", "https://")):
                    continue
                lk = str(_key).lower()
                if lk in {"path", "rp", "description_path", "descriptor_path", "device_description"}:
                    continue
                push_relative(v, agg_port, agg_https)

        services = md.get("services") if isinstance(md.get("services"), list) else []
        for svc in services:
            if not isinstance(svc, dict):
                continue
            stxt = svc.get("txt") if isinstance(svc.get("txt"), dict) else {}
            if not stxt:
                continue
            sn = str(svc.get("service", "")).lower()
            sport = int(svc.get("port", 0) or 0)
            if sport <= 0:
                sport = agg_port
            if "_https._tcp" in sn:
                svc_https = True
            elif "_http._tcp" in sn:
                svc_https = False
            else:
                svc_https = agg_https

            for _key, val in stxt.items():
                if not isinstance(val, str):
                    continue
                v = val.strip()
                if v.startswith(("http://", "https://")):
                    push(v)
            for pk in ("path", "rp", "description_path", "descriptor_path", "device_description"):
                raw = stxt.get(pk)
                if isinstance(raw, str) and raw.strip():
                    push_relative(raw, sport, svc_https)
            for key, val in stxt.items():
                if not isinstance(val, str):
                    continue
                v = val.strip()
                if ".xml" not in v.lower():
                    continue
                if v.startswith(("http://", "https://")):
                    continue
                lk = str(key).lower()
                if lk in {"path", "rp", "description_path", "descriptor_path", "device_description"}:
                    continue
                push_relative(v, sport, svc_https)

        return sorted(((q, u) for u, q in scored.items()), key=lambda t: t[0], reverse=True)

    def _best_mdns_descriptor_template(self, device: Device) -> str | None:
        """Full descriptor URL if mDNS TXT advertises a UPnP XML path (often faster than waiting for SSDP)."""
        if device.source != "mdns":
            return None
        md = device.metadata if isinstance(device.metadata, dict) else {}
        sip = str(device.ip).strip()
        if not sip:
            return None
        port, https = self._mdns_web_endpoint(md, device)
        candidates = self._collect_mdns_descriptor_candidates(md, sip, port, https)
        if not candidates:
            return None
        return candidates[0][1]

    def _resolve_anticipatory_descriptor_for_mdns(
        self, device: Device, sip: str
    ) -> tuple[str, AnticipatoryDescriptorSource] | None:
        """Choose descriptor GET URL for anticipatory fetch — **one precedence chain**.

        The HTTP GET target is not ambiguous: we never blend SSDP cache with mDNS as competing URLs.

        1. ``ssdp_profile_cache``: persisted SSDP ``LOCATION`` from disk profile cache (same path/query as
           prior SSDP observation); host rewritten to ``sip``.
        2. ``mdns_txt``: only when (1) has no usable ``ssdp_location`` — TXT may advertise the descriptor
           before SSDP has run / populated cache.
        """
        self._ssdp_profile_cache_by_ip, self._ssdp_profile_cache_emit_rows = (
            self._load_ssdp_profile_cache_by_ip()
        )
        row = self._ssdp_profile_cache_by_ip.get(sip)
        loc = row.get("ssdp_location") if isinstance(row, dict) else None
        loc_s = loc.strip() if isinstance(loc, str) and loc.strip() else ""
        if loc_s:
            url_new = _descriptor_url_with_ip(loc_s, sip)
            if url_new:
                return (url_new, "ssdp_profile_cache")
        mdns_tpl = self._best_mdns_descriptor_template(device)
        if mdns_tpl:
            url_new = _descriptor_url_with_ip(mdns_tpl, sip)
            if url_new:
                return (url_new, "mdns_txt")
        return None

    def _schedule_anticipatory_descriptor_fetch(self, device: Device) -> None:
        """Prefetch UPnP descriptor XML for mDNS hosts when we can build the URL early.

        Resolution uses :meth:`_resolve_anticipatory_descriptor_for_mdns` (single source-of-truth chain).
        """
        if device.source != "mdns" or not device.online:
            return
        ssdp = self._ssdp_discovery
        if ssdp is None:
            return
        k = device.key
        if k in self._anticipatory_fetch_attempted:
            return
        sip = str(device.ip).strip()
        if not sip or sip in {"0.0.0.0", "::"}:
            return

        resolved = self._resolve_anticipatory_descriptor_for_mdns(device, sip)
        if not resolved:
            return
        url_new, url_source = resolved

        self._anticipatory_fetch_attempted.add(k)
        self._logger.debug(
            "Anticipatory descriptor fetch scheduled ip=%s source=%s url=%s",
            sip,
            url_source,
            url_new,
        )

        def worker() -> None:
            try:
                xml_fields, raw_xml = ssdp.fetch_descriptor_xml(url_new)
            except Exception:
                self._mdns_logger.debug("Anticipatory descriptor fetch failed for %s", url_new, exc_info=True)
                return
            if not xml_fields and not raw_xml:
                return

            def apply_on_idle() -> None:
                self._apply_prefetched_descriptor(
                    k,
                    url_new,
                    xml_fields or {},
                    raw_xml,
                    url_source=url_source,
                )

            self._schedule_on_main_thread(apply_on_idle)

        threading.Thread(target=worker, name="NetNeighbor-anticipatory-xml", daemon=True).start()

    def _apply_prefetched_descriptor(
        self,
        device_key: str,
        fetch_url: str,
        xml_fields: dict,
        raw_xml: str | None,
        *,
        url_source: AnticipatoryDescriptorSource,
    ) -> None:
        if device_key in self._anticipatory_reentrant:
            return
        existing = self._devices.get(device_key)
        if existing is None or existing.source != "mdns":
            return
        self._anticipatory_reentrant.add(device_key)
        try:
            md = dict(existing.metadata) if isinstance(existing.metadata, dict) else {}
            prev_xf = md.get("xml_fields") if isinstance(md.get("xml_fields"), dict) else {}
            merged_xf = dict(prev_xf)
            for fk, fv in xml_fields.items():
                if fv is None:
                    continue
                if isinstance(fv, str) and not fv.strip():
                    continue
                merged_xf[fk] = fv.strip() if isinstance(fv, str) else fv
            md["xml_fields"] = merged_xf
            if raw_xml:
                md["xml"] = raw_xml
            md["anticipatory_descriptor_url"] = fetch_url
            md["anticipatory_descriptor_source"] = url_source
            name = existing.name
            fn = merged_xf.get("friendlyName") or merged_xf.get("displayName")
            if not self._has_effective_name_override(existing):
                if isinstance(fn, str) and fn.strip() and self._is_generic_discovery_name(name):
                    name = fn.strip()
            url = existing.url
            if not url:
                pu = merged_xf.get("presentationURL")
                if isinstance(pu, str) and pu.strip():
                    url = pu.strip()
            icon = existing.icon
            if not icon:
                iu = merged_xf.get("iconURL")
                if isinstance(iu, str) and iu.strip():
                    icon = iu.strip()
            updated = Device(
                name=name,
                ip=existing.ip,
                port=existing.port,
                type=existing.type,
                category=existing.category,
                source=existing.source,
                url=url,
                metadata=md,
                last_seen=existing.last_seen,
                online=existing.online,
                monitored=existing.monitored,
                icon=icon,
            )
            self._apply_field_mapping_rules(updated)
            self._apply_type_override(updated)
            self._apply_name_override(updated)
            self._apply_location_override(updated)
            self._apply_url_override(updated)
            self._apply_device_commands(updated)
            self._apply_custom_command_override(updated)
            self._apply_monitored_override(updated)
            self._devices[device_key] = updated
            self._run_location_reapply_sweep()
            self._notify()
        finally:
            self._anticipatory_reentrant.discard(device_key)

    def _stable_identity_available(self, device: Device) -> bool:
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        return bool(self._extract_uid(metadata) or self._extract_mac(metadata))

    def _pending_identity_key(self, device: Device) -> str:
        return f"{(device.source or '').strip().lower()}:{str(device.ip).strip()}:{int(device.port)}"

    def _should_hold_for_stable_identity(self, device: Device) -> bool:
        """Delay first appearance briefly until UID/MAC shows up, then release."""
        if device.source not in {"mdns", "ssdp"} or not bool(device.online):
            return False
        if self._stable_identity_available(device):
            pending_key = self._pending_identity_key(device)
            if pending_key in self._identity_pending:
                self._identity_pending.pop(pending_key, None)
                self._device_event_logger(device.source).debug(
                    "identity_resolved ip=%s port=%s source=%s",
                    device.ip,
                    device.port,
                    device.source,
                )
            return False

        now = datetime.now(timezone.utc)
        pending_key = self._pending_identity_key(device)
        first_seen, _prev = self._identity_pending.get(pending_key, (now, device))
        self._identity_pending[pending_key] = (first_seen, device)
        if now - first_seen < timedelta(seconds=_IDENTITY_HOLD_SECONDS):
            self._device_event_logger(device.source).debug(
                "identity_pending ip=%s port=%s source=%s held_for_ms=%s",
                device.ip,
                device.port,
                device.source,
                int((now - first_seen).total_seconds() * 1000),
            )
            return True
        self._device_event_logger(device.source).debug(
            "identity_pending_timeout ip=%s port=%s source=%s hold_s=%s",
            device.ip,
            device.port,
            device.source,
            _IDENTITY_HOLD_SECONDS,
        )
        self._identity_pending.pop(pending_key, None)
        return False

    def _emit_presence_hooks_if_transition(self, device: Device, prev_online: bool | None, now_online: bool) -> None:
        # Skip first-seen already-offline rows (monitoring placeholders, etc.).
        if now_online and prev_online is not True:
            transition: PresenceTransitionKind = "online"
        elif prev_online is True and not now_online:
            transition = "offline"
        else:
            return
        host_key = self._presence_dedupe_key(device)
        if host_key:
            if transition == "online":
                if host_key in self._presence_announced_hosts:
                    return
                self._presence_announced_hosts.add(host_key)
            else:
                self._presence_announced_hosts.discard(host_key)
        if not self._presence_hooks:
            return
        for hook in tuple(self._presence_hooks):
            try:
                hook(device, transition)
            except Exception:
                self._logger.exception("Presence transition hook failed (event=%s)", transition)

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

    def set_name_overrides(self, overrides: dict[str, str]) -> None:
        normalized: dict[str, str] = {}
        for key, value in overrides.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            value_norm = value.strip()
            if value_norm:
                normalized[key] = value_norm
        self._name_overrides = normalized

    def get_name_overrides(self) -> dict[str, str]:
        return dict(self._name_overrides)

    def set_url_overrides(self, overrides: dict[str, str]) -> None:
        normalized: dict[str, str] = {}
        for key, value in overrides.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            value_norm = value.strip()
            if value_norm:
                normalized[key] = value_norm
        self._url_overrides = normalized

    def get_url_overrides(self) -> dict[str, str]:
        return dict(self._url_overrides)

    def set_device_commands_overrides(self, overrides: object) -> None:
        """Bulk-load per-device commands from persisted preferences.
        Accepts new list format or legacy dict format (auto-migrated to override mode)."""
        normalized: dict[str, list[dict]] = {}
        if not isinstance(overrides, dict):
            self._device_commands = normalized
            return
        for key, raw in overrides.items():
            if not isinstance(key, str):
                continue
            # Legacy format: {scheme: {ip, port}} → convert to list with mode=override
            if isinstance(raw, dict):
                raw = [
                    {"scheme": s, "ip": ep.get("ip", ""), "port": ep.get("port", 0),
                     "mode": "override", "label": ""}
                    for s, ep in raw.items() if isinstance(ep, dict)
                ]
            if not isinstance(raw, list):
                continue
            valid = _normalize_command_list(raw)
            if valid:
                normalized[key] = valid
        self._device_commands = normalized

    def get_device_commands_overrides(self) -> dict[str, list[dict]]:
        return {k: [dict(c) for c in cmds] for k, cmds in self._device_commands.items()}

    def set_custom_command_overrides(self, overrides: dict[str, str]) -> None:
        """Bulk-load per-device custom command overrides from persisted preferences."""
        normalized: dict[str, str] = {}
        if isinstance(overrides, dict):
            for key, cmd in overrides.items():
                if isinstance(key, str) and isinstance(cmd, str) and cmd.strip():
                    normalized[key] = cmd.strip()
        self._custom_command_overrides = normalized

    def get_custom_command_overrides(self) -> dict[str, str]:
        return dict(self._custom_command_overrides)

    def set_location_overrides(self, overrides: dict[str, str]) -> None:
        normalized: dict[str, str] = {}
        for key, value in overrides.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            value_norm = value.strip()
            if value_norm:
                normalized[key] = value_norm
        self._location_overrides = normalized
        changed = False
        for device in self._devices.values():
            before = ""
            metadata = device.metadata if isinstance(device.metadata, dict) else {}
            value = metadata.get("user_location")
            if isinstance(value, str):
                before = value
            self._apply_location_override(device)
            metadata_after = device.metadata if isinstance(device.metadata, dict) else {}
            after_raw = metadata_after.get("user_location")
            after = after_raw if isinstance(after_raw, str) else ""
            if before != after:
                changed = True
        if self._run_location_reapply_sweep():
            changed = True
        if changed:
            self._notify()

    def get_location_overrides(self) -> dict[str, str]:
        return dict(self._location_overrides)

    def set_field_mapping_rules(self, rules: dict[str, dict[str, list[str]]]) -> None:
        normalized: dict[str, dict[str, list[str]]] = {}
        for key, value in rules.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            per_target: dict[str, list[str]] = {}
            for target in ("name", "location", "information"):
                raw = value.get(target)
                if not isinstance(raw, list):
                    continue
                cleaned = [str(x).strip() for x in raw if isinstance(x, str) and str(x).strip()]
                if cleaned:
                    per_target[target] = list(dict.fromkeys(cleaned))
            if per_target:
                normalized[key] = per_target
        self._field_mapping_rules = normalized
        changed = False
        for device in self._devices.values():
            before_name = device.name
            before_info = ""
            before_loc = ""
            md = device.metadata if isinstance(device.metadata, dict) else {}
            if isinstance(md.get("information"), str):
                before_info = md.get("information", "")
            if isinstance(md.get("user_location"), str):
                before_loc = md.get("user_location", "")
            self._apply_field_mapping_rules(device)
            self._apply_name_override(device)
            self._apply_location_override(device)
            md_after = device.metadata if isinstance(device.metadata, dict) else {}
            after_info = md_after.get("information") if isinstance(md_after.get("information"), str) else ""
            after_loc = md_after.get("user_location") if isinstance(md_after.get("user_location"), str) else ""
            if device.name != before_name or after_info != before_info or after_loc != before_loc:
                changed = True
        if changed:
            self._notify()

    def get_field_mapping_rules(self) -> dict[str, dict[str, list[str]]]:
        out: dict[str, dict[str, list[str]]] = {}
        for key, value in self._field_mapping_rules.items():
            out[key] = {target: list(paths) for target, paths in value.items()}
        return out

    def set_monitored_overrides(self, overrides: dict[str, bool]) -> None:
        normalized: dict[str, bool] = {}
        for key, value in overrides.items():
            if isinstance(key, str):
                normalized[key] = bool(value)
        self._monitored_overrides = normalized

    def get_monitored_overrides(self) -> dict[str, bool]:
        return dict(self._monitored_overrides)

    def set_hidden_overrides(self, overrides: dict[str, bool], *, notify: bool = True) -> None:
        normalized: dict[str, bool] = {}
        for key, value in overrides.items():
            if isinstance(key, str):
                normalized[key] = bool(value)
        self._hidden_overrides = normalized
        changed = False
        for device in self._devices_snapshot():
            before = device.hidden
            self._apply_hidden_override(device)
            if device.hidden != before:
                changed = True
        if changed and notify:
            self._notify()

    def get_hidden_overrides(self) -> dict[str, bool]:
        return dict(self._hidden_overrides)

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
                self._type_overrides.pop(endpoint_key, None)
                self._type_overrides[preferred_key] = str(device_type).strip().lower()
            self._apply_type_override(existing)
            new_key = existing.key
            if new_key != old_key:
                self._devices.pop(old_key, None)
                self._devices[new_key] = existing
            changed = True
        if changed:
            self._notify()

    def set_device_name_override(self, source: str, ip: str, port: int, device_name: str | None) -> None:
        changed = False
        for _old_key, existing in list(self._devices.items()):
            if existing.source != source or existing.ip != ip or existing.port != port:
                continue
            preferred_key = self._make_name_override_key_for_device(existing)
            endpoint_key = self._make_override_key(source, ip, port)
            if device_name is None or not str(device_name).strip():
                self._name_overrides.pop(preferred_key, None)
                self._name_overrides.pop(endpoint_key, None)
                existing.name = self._default_name_for_device(existing)
            else:
                self._name_overrides.pop(endpoint_key, None)
                self._name_overrides[preferred_key] = str(device_name).strip()
            self._apply_name_override(existing)
            changed = True
        if changed:
            self._notify()

    def set_device_location_override(self, source: str, ip: str, port: int, location: str | None) -> None:
        changed = False
        for _old_key, existing in list(self._devices.items()):
            if existing.source != source or existing.ip != ip or existing.port != port:
                continue
            preferred_key = self._make_name_override_key_for_device(existing)
            endpoint_key = self._make_override_key(source, ip, port)
            if location is None or not str(location).strip():
                self._location_overrides.pop(preferred_key, None)
                self._location_overrides.pop(endpoint_key, None)
            else:
                self._location_overrides.pop(endpoint_key, None)
                self._location_overrides[preferred_key] = str(location).strip()
            self._apply_location_override(existing)
            changed = True
        if changed:
            self._notify()

    def set_device_url_override(self, source: str, ip: str, port: int, url: str | None) -> None:
        changed = False
        for _old_key, existing in list(self._devices.items()):
            if existing.source != source or existing.ip != ip or existing.port != port:
                continue
            preferred_key = self._make_name_override_key_for_device(existing)
            endpoint_key = self._make_override_key(source, ip, port)
            if url is None or not str(url).strip():
                self._url_overrides.pop(preferred_key, None)
                self._url_overrides.pop(endpoint_key, None)
            else:
                self._url_overrides.pop(endpoint_key, None)
                self._url_overrides[preferred_key] = str(url).strip()
            self._apply_url_override(existing)
            changed = True
        if changed:
            self._notify()

    def set_device_commands(
        self, source: str, ip: str, port: int, commands: list[dict],
    ) -> None:
        """Replace all per-device commands for a specific device."""
        changed = False
        for _old_key, existing in list(self._devices.items()):
            if existing.source != source or existing.ip != ip or existing.port != port:
                continue
            preferred_key = self._make_name_override_key_for_device(existing)
            endpoint_key = self._make_override_key(source, ip, port)
            self._device_commands.pop(endpoint_key, None)
            valid = _normalize_command_list(commands) if isinstance(commands, list) else []
            if valid:
                self._device_commands[preferred_key] = valid
            else:
                self._device_commands.pop(preferred_key, None)
            self._apply_device_commands(existing)
            changed = True
        if changed:
            self._notify()

    def set_device_custom_command(self, source: str, ip: str, port: int, cmd: str | None) -> None:
        """Set or clear a per-device custom command override."""
        changed = False
        for _old_key, existing in list(self._devices.items()):
            if existing.source != source or existing.ip != ip or existing.port != port:
                continue
            preferred_key = self._make_name_override_key_for_device(existing)
            endpoint_key = self._make_override_key(source, ip, port)
            if cmd is None or not str(cmd).strip():
                self._custom_command_overrides.pop(preferred_key, None)
                self._custom_command_overrides.pop(endpoint_key, None)
            else:
                self._custom_command_overrides.pop(endpoint_key, None)
                self._custom_command_overrides[preferred_key] = str(cmd).strip()
            self._apply_custom_command_override(existing)
            changed = True
        if changed:
            self._notify()

    def set_device_field_mapping_rule(self, source: str, ip: str, port: int, target: str, field_path: str) -> None:
        target_norm = str(target).strip().lower()
        path_norm = str(field_path).strip()
        if target_norm not in {"name", "location", "information"} or not path_norm:
            return
        changed = False
        ip_norm = str(ip).strip()
        for _old_key, existing in list(self._devices.items()):
            if str(existing.ip).strip() != ip_norm:
                continue
            preferred_key = self._make_name_override_key_for_device(existing)
            rules = self._field_mapping_rules.get(preferred_key)
            if not isinstance(rules, dict):
                rules = {}
            bucket = rules.get(target_norm)
            if not isinstance(bucket, list):
                bucket = []
            if path_norm in bucket:
                continue
            bucket.append(path_norm)
            rules[target_norm] = bucket
            self._field_mapping_rules[preferred_key] = rules
            changed = True
            self._apply_field_mapping_rules(existing)
            self._apply_location_override(existing)
            self._apply_name_override(existing)
        if changed:
            self._notify()

    def remove_device_field_mapping_rule(
        self, source: str, ip: str, port: int, target: str, field_path: str | None = None
    ) -> None:
        target_norm = str(target).strip().lower()
        if target_norm not in {"name", "location", "information"}:
            return
        field_norm = str(field_path).strip() if isinstance(field_path, str) else ""
        changed = False
        ip_norm = str(ip).strip()
        for _old_key, existing in list(self._devices.items()):
            if str(existing.ip).strip() != ip_norm:
                continue
            preferred_key = self._make_name_override_key_for_device(existing)
            rules = self._field_mapping_rules.get(preferred_key)
            if not isinstance(rules, dict):
                continue
            bucket = rules.get(target_norm)
            if not isinstance(bucket, list) or not bucket:
                continue
            if field_norm:
                new_bucket = [x for x in bucket if x != field_norm]
            else:
                new_bucket = []
            if new_bucket == bucket:
                continue
            if new_bucket:
                rules[target_norm] = new_bucket
            else:
                rules.pop(target_norm, None)
            if rules:
                self._field_mapping_rules[preferred_key] = rules
            else:
                self._field_mapping_rules.pop(preferred_key, None)
            changed = True
            self._apply_field_mapping_rules(existing)
            self._apply_location_override(existing)
            self._apply_name_override(existing)
        if changed:
            self._notify()

    def get_device_field_mapping_rules(self, device: Device) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        ip_norm = str(device.ip).strip()
        for row in self._devices.values():
            if str(row.ip).strip() != ip_norm:
                continue
            preferred_key = self._make_name_override_key_for_device(row)
            value = self._field_mapping_rules.get(preferred_key)
            if not isinstance(value, dict):
                continue
            for target, paths in value.items():
                if not isinstance(paths, list):
                    continue
                bucket = out.get(target)
                if not isinstance(bucket, list):
                    bucket = []
                for path in paths:
                    if isinstance(path, str) and path.strip() and path not in bucket:
                        bucket.append(path)
                out[target] = bucket
        return out

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
        self._ssdp_logger.debug(
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
            self._ssdp_logger.debug("SSDP alias tracked: %s", value)
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
                self._ssdp_logger.debug("SSDP raw XML preserved from previous richer profile")
        elif isinstance(old_xml, str) and not isinstance(new_xml, str):
            merged["xml"] = old_xml
            self._ssdp_logger.debug("SSDP raw XML preserved because new payload has no XML")

        merged["location"] = self._pick_better_ssdp_location(
            old_meta.get("location"),
            new_meta.get("location"),
            old_rank,
            new_rank,
        )

        return merged

    def _ssdp_location_quality_score(self, value: str) -> int:
        s = (value or "").strip()
        if not s:
            return -1
        low = s.lower()
        score = 0
        if low.startswith(("http://", "https://")):
            score += 1
        if low.endswith(".xml"):
            score += 1
        if any(x in low for x in ("device-desc.xml", "description.xml", "/ssdp/")):
            score += 4
        if "/dd.xml" in low:
            score -= 2
        try:
            parsed = urlparse(s)
            if parsed.port in {80, 443, 8008, 1400, 5000}:
                score += 1
        except Exception:
            pass
        return score

    def _pick_better_ssdp_location(self, old_loc, new_loc, old_rank: int, new_rank: int) -> str | None:
        old_s = old_loc.strip() if isinstance(old_loc, str) and old_loc.strip() else ""
        new_s = new_loc.strip() if isinstance(new_loc, str) and new_loc.strip() else ""
        if not old_s:
            self._ssdp_logger.debug(
                "SSDP XML location selected(new-only) old=%r new=%r old_rank=%s new_rank=%s",
                old_s,
                new_s,
                old_rank,
                new_rank,
            )
            return new_s or None
        if not new_s:
            self._ssdp_logger.debug(
                "SSDP XML location selected(old-only) old=%r new=%r old_rank=%s new_rank=%s",
                old_s,
                new_s,
                old_rank,
                new_rank,
            )
            return old_s
        old_q = self._ssdp_location_quality_score(old_s)
        new_q = self._ssdp_location_quality_score(new_s)
        # If one LOCATION looks clearly better, prefer it even if XML profile rank differs.
        # This avoids sticky picks like random-port /dd.xml overriding DIAL device-desc.xml.
        quality_gap = 3
        if new_q >= old_q + quality_gap:
            self._ssdp_logger.debug(
                "SSDP XML location selected(new-quality-gap) old=%r new=%r old_rank=%s new_rank=%s old_q=%s new_q=%s",
                old_s,
                new_s,
                old_rank,
                new_rank,
                old_q,
                new_q,
            )
            return new_s
        if old_q >= new_q + quality_gap:
            self._ssdp_logger.debug(
                "SSDP XML location selected(old-quality-gap) old=%r new=%r old_rank=%s new_rank=%s old_q=%s new_q=%s",
                old_s,
                new_s,
                old_rank,
                new_rank,
                old_q,
                new_q,
            )
            return old_s
        if new_rank > old_rank:
            self._ssdp_logger.debug(
                "SSDP XML location selected(new-better-rank) old=%r new=%r old_rank=%s new_rank=%s",
                old_s,
                new_s,
                old_rank,
                new_rank,
            )
            return new_s
        if old_rank > new_rank:
            self._ssdp_logger.debug(
                "SSDP XML location selected(old-better-rank) old=%r new=%r old_rank=%s new_rank=%s",
                old_s,
                new_s,
                old_rank,
                new_rank,
            )
            return old_s
        if new_q > old_q:
            self._ssdp_logger.debug(
                "SSDP XML location selected(new-better-quality) old=%r new=%r old_rank=%s new_rank=%s old_q=%s new_q=%s",
                old_s,
                new_s,
                old_rank,
                new_rank,
                old_q,
                new_q,
            )
            return new_s
        # Keep previous location on tie for visual stability.
        self._ssdp_logger.debug(
            "SSDP XML location selected(old-stable) old=%r new=%r old_rank=%s new_rank=%s old_q=%s new_q=%s",
            old_s,
            new_s,
            old_rank,
            new_rank,
            old_q,
            new_q,
        )
        return old_s

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
        # Penalise generic placeholder names where friendlyName == modelName.
        # Manufacturers sometimes leave both fields identical (e.g. "Mediatek_MTXXXX"),
        # which is less useful than a user-visible name from a co-hosted DIAL service.
        friendly = str(xml_fields.get("friendlyName", "")).strip()
        model = str(xml_fields.get("modelName", "")).strip()
        if friendly and model and friendly.lower() == model.lower():
            score -= 3
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
            self._ssdp_logger.debug("SSDP name switched to newer profile name: %s", new_name)
            return new_name
        self._ssdp_logger.debug("SSDP name preserved from previous profile: %s", old_name)
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
                self._ssdp_logger.debug(
                    "SSDP endpoint match rejected due to MAC mismatch ip=%s port=%s old=%s new=%s",
                    candidate.ip,
                    candidate.port,
                    existing_mac,
                    candidate_mac,
                )
                continue
            if candidate_mac and not existing_mac:
                self._ssdp_logger.debug(
                    "SSDP endpoint match accepted and upgraded with MAC ip=%s port=%s mac=%s",
                    candidate.ip,
                    candidate.port,
                    candidate_mac,
                )
            if existing_mac and not candidate_mac:
                self._ssdp_logger.debug(
                    "SSDP endpoint match accepted using existing MAC ip=%s port=%s mac=%s",
                    candidate.ip,
                    candidate.port,
                    existing_mac,
                )
                return key, item
            return key, item
        return None, None

    def _find_existing_cross_protocol_by_identity(self, candidate: Device) -> tuple[str, Device] | tuple[None, None]:
        """Find an existing row from another protocol for the same host identity."""
        candidate_host_key = self._make_override_key_for_device(candidate)
        if not isinstance(candidate_host_key, str) or not candidate_host_key.startswith("host:"):
            return None, None
        for key, item in self._devices.items():
            if item.source == candidate.source:
                continue
            if self._make_override_key_for_device(item) != candidate_host_key:
                continue
            self._device_event_logger(candidate.source).debug(
                "Cross-protocol dedup matched host=%s old=%s/%s:%s new=%s/%s:%s",
                candidate_host_key,
                item.source,
                item.ip,
                item.port,
                candidate.source,
                candidate.ip,
                candidate.port,
            )
            return key, item
        return None, None

    def _cross_protocol_type_rank(self, device_type: str) -> int:
        t = (device_type or "unknown").strip().lower()
        return {
            "unknown": 0,
            "http": 12,
            "https": 12,
            "computer": 20,
            "esp32": 22,
            "router": 35,
            "nas": 38,
            "mediaserver": 34,
            "smartspeaker": 40,
            "networkprinter": 55,
            "multifunction_printer": 56,
            "printer": 55,
            "scanner": 52,
            "smarttv": 36,
            "smartdevice": 28,
            "camera": 30,
            "homeappliance": 26,
            "cnc": 30,
            "3dprinter": 32,
        }.get(t, 8)

    def _is_generic_discovery_name(self, name: str) -> bool:
        s = (name or "").strip().lower()
        if not s:
            return True
        prefixes = ("ssdp device ", "mdns device ", "router ", "media server ", "printer ")
        return any(s.startswith(prefix) for prefix in prefixes)

    def _protocol_merge_rank(self, source: str) -> int:
        """Lower rank = higher precedence (earlier in ``discovery.json`` ``merge.protocol_order``)."""
        s = (source or "").strip().lower()
        order = self._protocol_merge_order
        try:
            return order.index(s)
        except ValueError:
            return len(order)

    def _choose_cross_protocol_display_name(self, existing: Device, incoming: Device) -> str:
        """Prefer a non-generic name; if both are specific or both generic, prefer stronger protocol."""
        eg = self._is_generic_discovery_name(existing.name)
        ig = self._is_generic_discovery_name(incoming.name)
        if not eg and ig:
            return existing.name
        if eg and not ig:
            return incoming.name
        ir = self._protocol_merge_rank(incoming.source)
        er = self._protocol_merge_rank(existing.source)
        if ir < er:
            return incoming.name
        if er < ir:
            return existing.name
        return existing.name

    def _merge_cross_protocol_device(self, existing: Device, incoming: Device) -> Device:
        """Merge SSDP/mDNS rows that represent the same host."""
        old_meta = existing.metadata if isinstance(existing.metadata, dict) else {}
        new_meta = incoming.metadata if isinstance(incoming.metadata, dict) else {}
        merged_meta = dict(old_meta)
        # Keep per-protocol metadata snapshots to avoid losing diagnostics/details payloads.
        proto_meta = merged_meta.get("protocol_metadata") if isinstance(merged_meta.get("protocol_metadata"), dict) else {}
        proto_meta = dict(proto_meta)
        proto_meta[str(existing.source)] = dict(old_meta)
        proto_meta[str(incoming.source)] = dict(new_meta)
        merged_meta["protocol_metadata"] = proto_meta
        seen_sources = merged_meta.get("seen_sources") if isinstance(merged_meta.get("seen_sources"), list) else []
        merged_meta["seen_sources"] = sorted({str(x) for x in [*seen_sources, existing.source, incoming.source] if str(x).strip()})
        if isinstance(new_meta.get("user_location"), str) and new_meta.get("user_location", "").strip():
            merged_meta["user_location"] = new_meta.get("user_location").strip()

        old_rank = self._cross_protocol_type_rank(existing.type)
        new_rank = self._cross_protocol_type_rank(incoming.type)
        chosen_type = incoming.type if new_rank > old_rank else existing.type
        chosen_category = self._category_for_type(chosen_type)
        chosen_name = self._choose_cross_protocol_display_name(existing, incoming)
        chosen_icon = existing.icon
        if (not chosen_icon and incoming.icon) or new_rank > old_rank:
            chosen_icon = incoming.icon or chosen_icon
        chosen_url = existing.url or incoming.url
        chosen_last_seen = existing.last_seen if existing.last_seen >= incoming.last_seen else incoming.last_seen
        chosen_online = bool(existing.online or incoming.online)

        self._logger.debug(
            "Cross-protocol merge kept=%s/%s:%s merged=%s/%s:%s type=%s->%s",
            existing.source,
            existing.ip,
            existing.port,
            incoming.source,
            incoming.ip,
            incoming.port,
            existing.type,
            chosen_type,
        )

        return Device(
            name=chosen_name,
            ip=existing.ip,
            port=existing.port,
            type=chosen_type,
            category=chosen_category,
            source=existing.source,
            url=chosen_url,
            metadata=merged_meta,
            last_seen=chosen_last_seen,
            online=chosen_online,
            monitored=bool(existing.monitored),
            icon=chosen_icon,
        )

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
        if isinstance(mac, str) and mac.strip():
            return mac.strip().lower()

        def _mac_from_txt(txt: dict) -> str:
            if not isinstance(txt, dict):
                return ""
            for key in ("mac", "hardware-address", "hwaddr", "device-mac", "machine"):
                raw = txt.get(key)
                if isinstance(raw, str) and raw.strip():
                    return raw.strip().lower()
            return ""

        top = _mac_from_txt(metadata.get("txt") if isinstance(metadata.get("txt"), dict) else {})
        if top:
            return top
        services = metadata.get("services")
        if isinstance(services, list):
            for svc in services:
                if not isinstance(svc, dict):
                    continue
                got = _mac_from_txt(svc.get("txt") if isinstance(svc.get("txt"), dict) else {})
                if got:
                    return got
        return ""

    def _apply_type_override(self, device: Device) -> None:
        override_type = self._find_override_value(self._type_overrides, device)
        if not override_type:
            return
        device.type = override_type
        device.category = self._category_for_type(override_type)

    def _apply_name_override(self, device: Device) -> None:
        """Rule 0: apply saved name prefs last among the early pipeline (overrides rule 1 and mDNS)."""
        override_name = self._find_name_override_value(device)
        if not override_name:
            return
        device.name = override_name

    def _apply_url_override(self, device: Device) -> None:
        override_url = self._find_override_value(self._url_overrides, device)
        if not isinstance(device.metadata, dict):
            device.metadata = {}
        if isinstance(override_url, str) and override_url.strip():
            device.metadata["url_override"] = override_url.strip()
        else:
            device.metadata.pop("url_override", None)

    def _apply_device_commands(self, device: Device) -> None:
        cmds = self._find_override_value(self._device_commands, device)
        if not isinstance(device.metadata, dict):
            device.metadata = {}
        valid = _normalize_command_list(cmds) if isinstance(cmds, list) else []
        if valid:
            device.metadata["device_commands"] = valid
        else:
            device.metadata.pop("device_commands", None)

    def _apply_custom_command_override(self, device: Device) -> None:
        cmd = self._find_override_value(self._custom_command_overrides, device)
        if not isinstance(device.metadata, dict):
            device.metadata = {}
        if isinstance(cmd, str) and cmd.strip():
            device.metadata["custom_command"] = cmd.strip()
        else:
            device.metadata.pop("custom_command", None)

    def _clip_loc_log(self, value: str) -> str:
        return value if len(value) <= 120 else value[:117] + "..."

    def _store_location_preference_for_device(self, device: Device, location: str) -> bool:
        """Write identity-based location prefs (same key rules as set_device_location_override)."""
        loc = str(location).strip()
        if not loc or not is_plausible_room_location(loc):
            return False
        preferred_key = self._make_name_override_key_for_device(device)
        endpoint_key = self._make_override_key(device.source, device.ip, device.port)
        if self._location_overrides.get(preferred_key) == loc:
            return False
        self._location_overrides.pop(endpoint_key, None)
        self._location_overrides[preferred_key] = loc
        self._location_prefs_need_reapply = True
        return True

    def _consume_location_prefs_need_reapply(self) -> bool:
        if not self._location_prefs_need_reapply:
            return False
        self._location_prefs_need_reapply = False
        return True

    def _run_location_reapply_sweep(self) -> bool:
        """Re-apply location rules to all cached devices after prefs mutation; persist if needed."""
        any_round = False
        rounds = 0
        while self._consume_location_prefs_need_reapply() and rounds < 8:
            rounds += 1
            any_round = True
            for existing in list(self._devices.values()):
                self._apply_location_override(existing)
        if any_round and self._location_prefs_dirty_callback is not None:
            try:
                self._location_prefs_dirty_callback()
            except Exception:
                self._logger.exception("location_prefs_dirty_callback failed")
        return any_round

    def _apply_location_override(self, device: Device) -> None:
        if not isinstance(device.metadata, dict):
            device.metadata = {}
        dev_log = self._device_event_logger(device.source)
        cached_raw = self._find_location_override_value(device)
        discovered_raw = self._default_location_for_device(device)
        c_norm = cached_raw.strip() if isinstance(cached_raw, str) and cached_raw.strip() else ""
        d_norm = discovered_raw.strip() if isinstance(discovered_raw, str) and discovered_raw.strip() else ""

        chosen = ""

        if c_norm:
            if d_norm and d_norm != c_norm and is_plausible_room_location(d_norm):
                self._store_location_preference_for_device(device, d_norm)
                chosen = d_norm
                dev_log.debug(
                    "Location: discovery overrides cache ip=%s port=%s old=%r new=%r",
                    device.ip,
                    device.port,
                    self._clip_loc_log(c_norm),
                    self._clip_loc_log(d_norm),
                )
            else:
                chosen = c_norm
                dev_log.debug(
                    "Location: prefs cache ip=%s port=%s value=%r",
                    device.ip,
                    device.port,
                    self._clip_loc_log(chosen),
                )
        elif d_norm and is_plausible_room_location(d_norm):
            self._store_location_preference_for_device(device, d_norm)
            chosen = d_norm
            dev_log.debug(
                "Location: auto from discovery (persisted) ip=%s port=%s value=%r",
                device.ip,
                device.port,
                self._clip_loc_log(chosen),
            )
        else:
            dev_log.debug(
                "Location: empty ip=%s port=%s (no RoomName/xml_fields or TXT keys matched)",
                device.ip,
                device.port,
            )

        if chosen:
            device.metadata["user_location"] = chosen
        else:
            device.metadata.pop("user_location", None)

    def _apply_field_mapping_rules(self, device: Device) -> None:
        if not isinstance(device.metadata, dict):
            device.metadata = {}
        rules = self.get_device_field_mapping_rules(device)
        if not rules:
            return
        for target, paths in rules.items():
            chosen = ""
            for raw in paths:
                value = self._mapped_value_for_field_path(device, raw)
                if isinstance(value, str) and value.strip():
                    chosen = value.strip()
                    break
            if not chosen:
                continue
            if target == "name":
                device.name = chosen
            elif target == "location":
                if is_plausible_room_location(chosen):
                    device.metadata["user_location"] = chosen
            elif target == "information":
                device.metadata["information"] = chosen

    def _mapped_value_for_field_path(self, device: Device, field_path: str) -> str:
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        raw = str(field_path or "").strip()
        if ":" not in raw:
            return ""
        prefix, rest = raw.split(":", 1)
        key = rest.strip()
        if not key:
            return ""
        if prefix == "txt":
            def _txt_search(meta: dict) -> str:
                txt = meta.get("txt") if isinstance(meta.get("txt"), dict) else {}
                for rk, rv in txt.items():
                    if isinstance(rk, str) and str(rk).strip().lower() == key.lower() and isinstance(rv, str):
                        return rv
                services = meta.get("services") if isinstance(meta.get("services"), list) else []
                for svc in services:
                    if not isinstance(svc, dict):
                        continue
                    st = svc.get("txt") if isinstance(svc.get("txt"), dict) else {}
                    for rk, rv in st.items():
                        if isinstance(rk, str) and str(rk).strip().lower() == key.lower() and isinstance(rv, str):
                            return rv
                return ""
            found = _txt_search(metadata)
            if found:
                return found
            # Fall back to sibling devices with the same IP (e.g. SSDP primary + mDNS TXT data)
            ip_norm = str(device.ip).strip()
            for sibling in self._devices.values():
                if sibling is device or str(sibling.ip).strip() != ip_norm:
                    continue
                sib_meta = sibling.metadata if isinstance(sibling.metadata, dict) else {}
                found = _txt_search(sib_meta)
                if found:
                    return found
            return ""
        if prefix == "xml":
            xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
            for rk, rv in xml_fields.items():
                if isinstance(rk, str) and str(rk).strip().lower() == key.lower() and isinstance(rv, str):
                    return rv
            return ""
        if prefix == "meta":
            for rk, rv in metadata.items():
                if isinstance(rk, str) and str(rk).strip().lower() == key.lower() and isinstance(rv, str):
                    return rv
            return ""
        return ""

    def _apply_monitored_override(self, device: Device) -> None:
        override_value = self._find_monitored_override_value(device)
        if override_value is not None:
            device.monitored = bool(override_value)

    def _apply_hidden_override(self, device: Device) -> None:
        override_value = self._find_hidden_override_value(device)
        if override_value is not None:
            device.hidden = bool(override_value)

    def _find_override_value(self, store: dict, device: Device):
        preferred_key = self._make_override_key_for_device(device)
        if preferred_key in store:
            return store[preferred_key]
        endpoint_key = self._make_override_key(device.source, device.ip, device.port)
        if endpoint_key in store:
            return store[endpoint_key]
        hit = self._prefs_entry_same_source_ipv4_any_port(store, device)
        if hit is not None:
            return hit
        return self._prefs_entry_for_host_ip_fallback(store, device)

    def _find_name_override_value(self, device: Device) -> str | None:
        preferred_key = self._make_name_override_key_for_device(device)
        value = self._name_overrides.get(preferred_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        endpoint_key = self._make_override_key(device.source, device.ip, device.port)
        value = self._name_overrides.get(endpoint_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        hit_ep = self._prefs_entry_same_source_ipv4_any_port(self._name_overrides, device)
        if isinstance(hit_ep, str) and hit_ep.strip():
            return hit_ep.strip()
        hit = self._prefs_entry_for_host_ip_fallback(self._name_overrides, device)
        if isinstance(hit, str) and hit.strip():
            return hit.strip()
        return None

    def _has_effective_name_override(self, device: Device, prior_row: Device | None = None) -> bool:
        """Rule 0: user-renamed devices (prefs) beat SSDP cache, mDNS, and descriptor-derived names."""
        if self._find_name_override_value(device) is not None:
            return True
        if prior_row is not None and self._find_name_override_value(prior_row) is not None:
            return True
        return False

    def _find_location_override_value(self, device: Device) -> str | None:
        preferred_key = self._make_name_override_key_for_device(device)
        value = self._location_overrides.get(preferred_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        endpoint_key = self._make_override_key(device.source, device.ip, device.port)
        value = self._location_overrides.get(endpoint_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        hit_ep = self._prefs_entry_same_source_ipv4_any_port(self._location_overrides, device)
        if isinstance(hit_ep, str) and hit_ep.strip():
            return hit_ep.strip()
        hit = self._prefs_entry_for_host_ip_fallback(self._location_overrides, device)
        if isinstance(hit, str) and hit.strip():
            return hit.strip()
        return None

    def _prefs_entry_for_host_ip_fallback(self, store: dict, device: Device):
        """Prefs may use host:ip:… while identity later resolves to host:uid:… / host:mac:… (richer mDNS)."""
        ip_only = str(device.ip).strip()
        if not ip_only or ip_only == "0.0.0.0":
            return None
        host_ip_key = f"host:ip:{ip_only}"
        if self._make_override_key_for_device(device) == host_ip_key:
            return None
        if host_ip_key in store:
            return store[host_ip_key]
        return None

    def _prefs_entry_same_source_ipv4_any_port(self, store: dict, device: Device):
        """Match `mdns:192.168.1.10:8611` when aggregated row moves to `:631`, etc. (IPv4-only)."""
        sip = str(device.ip).strip()
        if sip in {"", "0.0.0.0"}:
            return None
        try:
            if not isinstance(ipaddress.ip_address(sip), ipaddress.IPv4Address):
                return None
        except ValueError:
            return None
        prefix = f"{(device.source or '').strip().lower()}:{sip}:"
        matches: list[str] = []
        for key in store:
            if isinstance(key, str) and key.startswith(prefix):
                tail = key[len(prefix) :]
                if tail.isdigit():
                    matches.append(key)
        if not matches:
            return None

        endpoint_exact = prefix + str(int(device.port))
        if endpoint_exact in store:
            return store.get(endpoint_exact)
        widest = max(matches, key=lambda k: int(k.rsplit(":", 1)[-1]))
        return store.get(widest)

    def _supplement_missing_user_location(self, device: Device, prior_row: Device | None) -> None:
        """Stabilise location when fresh payload lacks room info but cache already had one."""
        if device.source not in {"mdns", "ssdp"}:
            return
        if not isinstance(device.metadata, dict):
            device.metadata = {}
        cur = device.metadata.get("user_location")
        if isinstance(cur, str) and cur.strip():
            return
        sip = str(device.ip).strip()
        if not sip or sip == "0.0.0.0":
            return
        if prior_row is not None and prior_row.source == device.source and prior_row.key == device.key:
            dm = prior_row.metadata if isinstance(prior_row.metadata, dict) else {}
            preserved = dm.get("user_location")
            if isinstance(preserved, str) and preserved.strip():
                ps = preserved.strip()
                if is_plausible_room_location(ps):
                    device.metadata["user_location"] = ps
                    self._device_event_logger(device.source).debug(
                        "Location: %s carry-over from prior row ip=%s value=%r",
                        device.source,
                        sip,
                        ps if len(ps) <= 120 else ps[:117] + "...",
                    )
                    return
        if device.source == "ssdp":
            for row in self._devices.values():
                if row is prior_row:
                    continue
                if row.source != "ssdp" or row.key == device.key:
                    continue
                if str(row.ip).strip() != sip:
                    continue
                dm = row.metadata if isinstance(row.metadata, dict) else {}
                borrowed = dm.get("user_location")
                if isinstance(borrowed, str) and borrowed.strip():
                    bs = borrowed.strip()
                    if not is_plausible_room_location(bs):
                        continue
                    device.metadata["user_location"] = bs
                    self._ssdp_logger.debug(
                        "Location: SSDP borrowed same-ip ip=%s from key=%s value=%r",
                        sip,
                        row.key,
                        bs if len(bs) <= 120 else bs[:117] + "...",
                    )
                    return
            return
        for row in self._devices.values():
            if row is prior_row:
                continue
            if row.source != "mdns" or row.key == device.key:
                continue
            if str(row.ip).strip() != sip:
                continue
            dm = row.metadata if isinstance(row.metadata, dict) else {}
            borrowed = dm.get("user_location")
            if isinstance(borrowed, str) and borrowed.strip():
                bs = borrowed.strip()
                if not is_plausible_room_location(bs):
                    continue
                device.metadata["user_location"] = bs
                self._mdns_logger.debug(
                    "Location: mDNS borrowed same-ip ip=%s from key=%s value=%r",
                    sip,
                    row.key,
                    bs if len(bs) <= 120 else bs[:117] + "...",
                )
                return

    def _make_override_key(self, source: str, ip: str, port: int) -> str:
        return f"{source}:{ip}:{int(port)}"

    def _mac_for_device_identity(self, device: Device) -> str:
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        raw = self._extract_mac(metadata)
        normalized = normalize_mac_for_bundle_merge(raw) if raw else ""
        if normalized:
            return normalized
        hit = lookup_mac_from_neighbor_cache(str(device.ip).strip())
        return normalize_mac_for_bundle_merge(hit) if hit else ""

    def _normalized_monitored_uid(self, device: Device) -> str:
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        uid = self._extract_uid(metadata)
        return normalize_monitored_uid(uid) if uid else ""

    def _monitored_name_for_identity(self, device: Device) -> str:
        override = self._find_name_override_value(device)
        if override:
            return normalize_monitored_name(override)
        return normalize_monitored_name(device.name or "")

    def _monitored_lookup_keys_for_device(self, device: Device) -> list[str]:
        """Prefs aliases for this row: MAC → UID → name → IP (then legacy endpoint keys)."""
        keys: list[str] = []
        seen: set[str] = set()

        def _add(key: str) -> None:
            if key and key not in seen:
                seen.add(key)
                keys.append(key)

        mac = self._mac_for_device_identity(device)
        if mac:
            _add(f"host:mac:{mac}")
        uid = self._normalized_monitored_uid(device)
        if uid:
            _add(f"host:uid:{uid}")
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        raw_uid = self._extract_uid(metadata)
        if raw_uid:
            _add(f"host:uid:{raw_uid.strip().lower()}")
        name = self._monitored_name_for_identity(device)
        if name:
            _add(f"host:name:{name}")
        ip = str(device.ip).strip()
        if ip and ip != "0.0.0.0":
            _add(f"host:ip:{ip}")
        _add(self._make_override_key(device.source, device.ip, device.port))
        return keys

    def _canonical_monitored_keys(self, device: Device) -> list[str]:
        """Persisted follow key preference: MAC, UUID, name, IP (no endpoint/port keys)."""
        out: list[str] = []
        mac = self._mac_for_device_identity(device)
        if mac:
            out.append(f"host:mac:{mac}")
        uid = self._normalized_monitored_uid(device)
        if uid:
            out.append(f"host:uid:{uid}")
        name = self._monitored_name_for_identity(device)
        if name:
            out.append(f"host:name:{name}")
        ip = str(device.ip).strip()
        if ip and ip != "0.0.0.0":
            out.append(f"host:ip:{ip}")
        return out

    def _canonical_monitored_key(self, device: Device) -> str:
        keys = self._canonical_monitored_keys(device)
        if keys:
            return keys[0]
        return self._make_override_key(device.source, device.ip, device.port)

    def _presence_dedupe_key(self, device: Device) -> str | None:
        return self._canonical_monitored_key(device)

    def _find_monitored_override_value(self, device: Device) -> bool | None:
        return self._find_host_flag_override_value(self._monitored_overrides, device)

    def _find_hidden_override_value(self, device: Device) -> bool | None:
        return self._find_host_flag_override_value(self._hidden_overrides, device)

    def _find_host_flag_override_value(self, store: dict[str, bool], device: Device) -> bool | None:
        for key in self._monitored_lookup_keys_for_device(device):
            if key in store:
                return store[key]
        legacy = self._find_override_value(store, device)
        if legacy is not None:
            return bool(legacy)
        return None

    def _device_matches_monitored_key(self, device: Device, host_key: str) -> bool:
        return host_key in self._monitored_lookup_keys_for_device(device)

    def _device_matches_hidden_key(self, device: Device, host_key: str) -> bool:
        return host_key in self._monitored_lookup_keys_for_device(device)

    def bundle_is_hidden(self, ip: str, port: int) -> bool:
        anchor = self._anchor_device_for_endpoint(ip, port)
        if anchor is not None:
            return self._find_hidden_override_value(anchor) is True
        host_key = f"host:ip:{str(ip).strip()}"
        return bool(self._hidden_overrides.get(host_key))

    def canonical_host_identity_key(self, ip: str, port: int) -> str:
        anchor = self._anchor_device_for_endpoint(ip, port)
        if anchor is not None:
            return self._canonical_monitored_key(anchor)
        sip = str(ip).strip()
        return f"host:ip:{sip}" if sip and sip != "0.0.0.0" else ""

    def _anchor_device_for_endpoint(self, ip: str, port: int) -> Device | None:
        sip = str(ip).strip()
        if not sip or sip == "0.0.0.0":
            return None
        exact: list[Device] = []
        same_ip: list[Device] = []
        for device in self._devices_snapshot():
            if str(device.ip).strip() != sip:
                continue
            same_ip.append(device)
            if int(device.port) == int(port):
                exact.append(device)
        if exact:
            for preferred in ("ssdp", "mdns", "wsd", "wsdd", "nmb"):
                for device in exact:
                    if (device.source or "").strip().lower() == preferred:
                        return device
            return exact[0]
        if same_ip:
            for preferred in ("ssdp", "mdns", "wsd", "wsdd", "nmb"):
                for device in same_ip:
                    if (device.source or "").strip().lower() == preferred:
                        return device
            return same_ip[0]
        return None

    def _make_override_key_for_device(self, device: Device) -> str:
        uid = self._extract_uid(device.metadata)
        if uid:
            return f"host:uid:{uid}"
        mac = self._extract_mac(device.metadata)
        if mac:
            return f"host:mac:{mac}"
        ip = str(device.ip).strip()
        if ip and ip != "0.0.0.0":
            return f"host:ip:{ip}"
        return self._make_override_key(device.source, device.ip, device.port)

    def _make_name_override_key_for_device(self, device: Device) -> str:
        return self._make_override_key_for_device(device)

    def _extract_uid(self, metadata: dict) -> str:
        if not isinstance(metadata, dict):
            return ""
        xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
        txt_fields = metadata.get("txt") if isinstance(metadata.get("txt"), dict) else {}
        usn = metadata.get("usn")
        if isinstance(usn, str) and usn.strip():
            return upnp_identity_from_usn(usn)
        wsd_epr = metadata.get("wsd_epr")
        if isinstance(wsd_epr, str) and wsd_epr.strip():
            u = uuid_urn_if_present(wsd_epr)
            if u:
                return u
        wsdd_uri = metadata.get("wsdd_uri")
        if isinstance(wsdd_uri, str) and wsdd_uri.strip():
            u = uuid_urn_if_present(wsdd_uri)
            if u:
                return u
        udn_raw = xml_fields.get("UDN")
        if isinstance(udn_raw, str) and udn_raw.strip():
            hit = upnp_identity_from_udn(udn_raw)
            if hit:
                return hit
        candidates = [
            xml_fields.get("UDN"),
            metadata.get("udn"),
            txt_fields.get("uuid"),
            txt_fields.get("udn"),
            txt_fields.get("id"),
            txt_fields.get("deviceid"),
            txt_fields.get("device_id"),
            txt_fields.get("serial"),
            txt_fields.get("serialnumber"),
        ]
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        return ""

    def _default_name_for_device(self, device: Device) -> str:
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
        txt_fields = metadata.get("txt") if isinstance(metadata.get("txt"), dict) else {}
        ip = str(device.ip).strip() or "0.0.0.0"

        for key in ("friendlyName", "displayName"):
            value = xml_fields.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        if device.source == "mdns":
            for key in ("name", "fn", "friendlyname", "friendly_name", "device"):
                value = txt_fields.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            server_raw = metadata.get("server")
            if isinstance(server_raw, str) and server_raw.strip():
                normalized = server_raw.strip().removesuffix(".local.").removesuffix(".local").strip(".")
                if normalized:
                    base = normalized.replace("-", " ")
                    prod = ""
                    if isinstance(txt_fields, dict):
                        for tk, tv in txt_fields.items():
                            if isinstance(tk, str) and tk.lower() == "product" and isinstance(tv, str) and tv.strip():
                                prod = tv.strip()
                                break
                    if prod:
                        return f"{base} ({prod})"
                    return base
            for key in ("model", "mdl", "md", "product"):
                value = txt_fields.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            hostname = metadata.get("hostname")
            if isinstance(hostname, str) and hostname.strip():
                normalized = hostname.strip().removesuffix(".local.").removesuffix(".local").strip(".")
                if normalized:
                    return normalized.replace("-", " ")
            return f"mDNS Device {ip}"

        if device.source == "ssdp":
            server = str(metadata.get("server", ""))
            st = str(metadata.get("st", ""))
            server_low = server.lower()
            st_low = st.lower()
            if "router" in server_low or "wan" in st_low:
                return f"Router {ip}"
            if "mediaserver" in st_low or "dlna" in server_low:
                return f"Media Server {ip}"
            if "printer" in st_low:
                return f"Printer {ip}"
            return f"SSDP Device {ip}"

        return str(device.name).strip() or "Unknown"

    def _default_location_for_device(self, device: Device) -> str:
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
        txt_fields = metadata.get("txt") if isinstance(metadata.get("txt"), dict) else {}

        # Sonos commonly exposes a room label in SSDP XML fields.
        for value in (xml_fields.get("RoomName"), xml_fields.get("roomName"), metadata.get("RoomName"), metadata.get("roomName")):
            if isinstance(value, str) and value.strip():
                s = value.strip()
                if is_plausible_room_location(s):
                    return s

        def txt_by_key_ci(txt: dict, *wanted_lower: str) -> str:
            index: dict[str, str] = {}
            if isinstance(txt, dict):
                for rk, rv in txt.items():
                    if isinstance(rk, str) and isinstance(rv, str):
                        index[rk.lower()] = rv
            for wl in wanted_lower:
                cand = index.get(wl.lower())
                if isinstance(cand, str) and cand.strip():
                    s = cand.strip()
                    if is_plausible_room_location(s):
                        return s
            return ""

        loc = txt_by_key_ci(
            txt_fields,
            "roomname",
            "room_name",
            "room",
            "location",
            "locationname",
            "location_name",
            "zonename",
            "zone_name",
            "zone",
        )
        if loc:
            return loc
        services = metadata.get("services") if isinstance(metadata.get("services"), list) else []
        for svc in services:
            if not isinstance(svc, dict):
                continue
            st = svc.get("txt") if isinstance(svc.get("txt"), dict) else {}
            loc = txt_by_key_ci(st, "roomname", "room_name", "room", "location", "locationname", "zonename", "zone")
            if loc:
                return loc
        return ""

    def _category_for_type(self, device_type: str) -> str:
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
            "esp32": _("ESP3D Devices"),
            "unknown": _("Unknown Devices"),
        }.get(device_type, _("Unknown Devices"))

    def set_bundle_monitored(self, ip: str, port: int, monitored: bool) -> None:
        """Follow/unfollow a UI bundle (persisted by MAC, else UPnP UID, else IP)."""
        anchor = self._anchor_device_for_endpoint(ip, port)
        host_key = self._canonical_monitored_key(anchor) if anchor is not None else f"host:ip:{ip}"
        if monitored:
            self._monitored_overrides[host_key] = True
        else:
            keys_to_drop: set[str] = {host_key}
            for device in self._devices_snapshot():
                if anchor is not None:
                    if not self._device_matches_monitored_key(device, host_key):
                        continue
                elif str(device.ip).strip() != str(ip).strip():
                    continue
                keys_to_drop.update(self._monitored_lookup_keys_for_device(device))
            for key in keys_to_drop:
                self._monitored_overrides.pop(key, None)
        changed = False
        for device in self._devices_snapshot():
            if anchor is not None:
                if not self._device_matches_monitored_key(device, host_key):
                    continue
            elif str(device.ip).strip() != str(ip).strip():
                continue
            if device.monitored != monitored:
                device.monitored = monitored
                changed = True
        if changed:
            self._notify()

    def set_host_monitored(self, ip: str, monitored: bool) -> None:
        """Backward-compatible wrapper (uses first row at ``ip`` for identity)."""
        anchor = self._anchor_device_for_endpoint(ip, 0)
        port = int(anchor.port) if anchor is not None else 0
        self.set_bundle_monitored(ip, port, monitored)

    def set_device_monitored(self, device_key: str, monitored: bool) -> None:
        device = self._devices.get(device_key)
        if device is None:
            return
        self.set_bundle_monitored(str(device.ip), int(device.port), monitored)

    def set_bundle_hidden(self, ip: str, port: int, hidden: bool, *, notify: bool = True) -> None:
        """Hide/show a UI bundle (same identity keys as follow/monitor)."""
        anchor = self._anchor_device_for_endpoint(ip, port)
        host_key = self._canonical_monitored_key(anchor) if anchor is not None else f"host:ip:{ip}"
        if hidden:
            self._hidden_overrides[host_key] = True
        else:
            keys_to_drop: set[str] = {host_key}
            for device in self._devices_snapshot():
                if anchor is not None:
                    if not self._device_matches_hidden_key(device, host_key):
                        continue
                elif str(device.ip).strip() != str(ip).strip():
                    continue
                keys_to_drop.update(self._monitored_lookup_keys_for_device(device))
            for key in keys_to_drop:
                self._hidden_overrides.pop(key, None)
        changed = False
        for device in self._devices_snapshot():
            if anchor is not None:
                if not self._device_matches_hidden_key(device, host_key):
                    continue
            elif str(device.ip).strip() != str(ip).strip():
                continue
            if device.hidden != hidden:
                device.hidden = hidden
                changed = True
        if changed and notify:
            self._notify()

    def _notify(self) -> None:
        if not self._listeners:
            return
        self._logger.debug(
            "Publishing %d devices to %d listeners (debounce %.0fms)",
            len(self._devices),
            len(self._listeners),
            _NOTIFY_DEBOUNCE_SECONDS * 1000.0,
        )
        with self._notify_debounce_lock:
            if self._notify_debounce_timer is not None:
                self._notify_debounce_timer.cancel()
                self._notify_debounce_timer = None
            self._notify_debounce_timer = threading.Timer(
                _NOTIFY_DEBOUNCE_SECONDS,
                self._notify_emit_debounced,
            )
            self._notify_debounce_timer.daemon = True
            self._notify_debounce_timer.start()

    def _notify_emit_debounced(self) -> None:
        if self._stopped:
            return
        with self._notify_debounce_lock:
            self._notify_debounce_timer = None
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
