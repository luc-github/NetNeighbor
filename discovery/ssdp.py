# File ssdp.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Minimal live SSDP discovery (M-SEARCH + response parsing)."""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import logging
from pathlib import Path
import queue
import select
import socket
import struct
import sys
import threading
import time
from urllib.error import URLError
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen
import xml.etree.ElementTree as ET
from collections.abc import Callable

from discovery.base import BaseDiscovery
from utils.discovery_cache import load_discovery_cache, save_discovery_cache
from utils.user_config_overlay import merge_ssdp_rules_overlays

_SSDP_ADDR = ("239.255.255.250", 1900)
# Multicast M-SEARCH targets; unicast directed searches use the same ST list.
_SSDP_SEARCH_ST: tuple[str, ...] = (
    "ssdp:all",
    "upnp:rootdevice",
    "urn:dial-multiscreen-org:service:dial:1",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:device:MediaServer:1",
    "urn:schemas-upnp-org:device:MediaPlayer:1",
)
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
# Recv thread only enqueues; HTTP/XML runs on workers so M-SEARCH bursts are not dropped.
# Windows tends toward slower LAN HTTP and larger SSDP bursts per device (many ST values), so
# use more workers and a deeper queue than POSIX — same code path, OS-tuned defaults.
_RX_QUEUE_MAXSIZE = 4096 if sys.platform == "win32" else 2048
_RX_WORKER_COUNT = 4 if sys.platform == "win32" else 2
# Drop near-duplicate SSDP replies (same USN + LOCATION) from the same host within this window.
# One device often answers each M-SEARCH for several ST targets; dedup avoids serial XML fetches.
_RX_DEDUP_WINDOW_S = 0.65
_RX_DEDUP_PRUNE_AGE_S = 5.0
_RX_DEDUP_MAX_KEYS = 2000


def _resolve_ssdp_device_ip(parsed_hostname: str | None, packet_source_ip: str) -> str:
    """Use numeric IP from LOCATION when present; else the SSDP packet source (not DNS names)."""
    fb = str(packet_source_ip).strip()
    if not parsed_hostname or not str(parsed_hostname).strip():
        return fb if fb else "0.0.0.0"
    host = str(parsed_hostname).strip()
    inner = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        addr = ipaddress.ip_address(inner)
        return addr.compressed
    except ValueError:
        pass
    if fb and fb not in {"0.0.0.0", "::"}:
        return fb
    return host


# Exclude host-only / hypervisor switches from SSDP multicast join & send — they are not the LAN
# where UPnP devices live, and on Windows they can starve or mis-order stack behavior vs. real NICs.
_SSDP_MULTICAST_SKIP_SUBNETS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.ip_network("192.168.56.0/24"),  # VirtualBox host-only
    ipaddress.ip_network("192.168.53.0/24"),  # common Hyper-V / third-party virtual NIC
    ipaddress.ip_network("192.168.122.0/24"),  # libvirt virbr0
    ipaddress.ip_network("192.168.137.0/24"),  # Windows ICS / hotspot host
)


def _local_ipv4_multicast_ifaces() -> list[str]:
    """Local IPv4 addresses for SSDP multicast join and per-interface M-SEARCH (multi-homed hosts).

    On Windows, a single INADDR_ANY membership plus default multicast route often leaves LAN
    traffic on a virtual adapter (Hyper-V/WSL); explicit joins and ``IP_MULTICAST_IF`` steer
    discovery toward the real subnet.

    Virtual host-only subnets (VirtualBox, Hyper-V internal, etc.) are skipped so M-SEARCH
    multicast is not pinned to adapters that do not reach household UPnP devices.
    """
    preferred: str | None = None
    found: set[str] = set()
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("203.0.113.1", 9))
            ip = probe.getsockname()[0]
            if ip:
                preferred = ip
                found.add(ip)
        finally:
            probe.close()
    except OSError:
        pass
    try:
        for res in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM):
            ip = res[4][0]
            if ip:
                found.add(ip)
    except OSError:
        pass
    out: list[str] = []
    for ip in sorted(found):
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not isinstance(a, ipaddress.IPv4Address) or a.is_loopback:
            continue
        if a.is_multicast or a.is_unspecified:
            continue
        if a.is_link_local:
            continue  # SSDP discovery targets routed LAN; skip 169.254 noise
        out.append(a.compressed)

    def _in_skip_net(ip_s: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip_s)
        except ValueError:
            return True
        return any(addr in net for net in _SSDP_MULTICAST_SKIP_SUBNETS)

    filtered = [x for x in out if not _in_skip_net(x)]
    if not filtered:
        filtered = list(out)

    if preferred and preferred in filtered:
        rest = sorted(x for x in filtered if x != preferred)
        return [preferred, *rest]
    return sorted(filtered)


def _descriptor_xml_host_key(location: str) -> str:
    """Host:port part of SSDP descriptor URLs for throttle/coalesce bucketing.

    Including the port prevents cross-port XML substitution: a device that serves
    genuinely different descriptors on different ports (e.g. DIAL on :8008 and
    tvdevice on :56790) must not have its XML coalesced — they hold different
    friendlyName values and merging them produces wrong device names.
    """
    p = urlparse((location or "").strip())
    raw = (p.hostname or "").strip()
    if not raw:
        return ""
    inner = raw[1:-1] if raw.startswith("[") and raw.endswith("]") else raw
    try:
        addr = ipaddress.ip_address(inner)
        host = addr.compressed
    except ValueError:
        host = raw.lower()
    port = p.port
    if port:
        return f"{host}:{port}"
    return host


def _sonos_device_description_alternate_url(location: str | None) -> str | None:
    """Map Sonos ``group_description.xml`` LOCATION to sibling ``device_description.xml``.

    Group XML is small and often lacks ``roomName`` / icon list; the device descriptor has them.
    Without a follow-up fetch, the UI waits for a later NOTIFY or refresh — often tens of seconds.
    """
    if not location:
        return None
    s = location.strip()
    lower = s.lower()
    needle = "group_description.xml"
    pos = lower.find(needle)
    if pos < 0:
        return None
    return f"{s[:pos]}device_description.xml{s[pos + len(needle) :]}"


class SSDPDiscovery(BaseDiscovery):
    def __init__(
        self,
        rules_enabled: bool = True,
        query_interval_seconds: int | None = None,
        mx_seconds: int | None = None,
        descriptor_http_min_interval_seconds: float | None = None,
        msearch_directed_ips: list[str] | None = None,
        ephemeral_msearch_probe_ips: Callable[[], list[str]] | None = None,
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
        self._send_socket: socket.socket | None = None
        self._recv_socket: socket.socket | None = None
        self._listen_thread: threading.Thread | None = None
        self._rx_queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=_RX_QUEUE_MAXSIZE)
        self._rx_worker_threads: list[threading.Thread] = []
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
        self._msearch_directed_ips: list[str] = []
        if msearch_directed_ips:
            for raw in msearch_directed_ips:
                if not isinstance(raw, str) or not raw.strip():
                    continue
                try:
                    addr = ipaddress.ip_address(raw.strip())
                except ValueError:
                    continue
                if isinstance(addr, ipaddress.IPv4Address):
                    self._msearch_directed_ips.append(addr.compressed)
        self._ephemeral_probe_resolver = ephemeral_msearch_probe_ips
        self._multicast_if_ipv4s: list[str] = []
        self._rx_dedup: dict[str, float] = {}
        self._rx_dedup_lock = threading.Lock()
        # Separate thread pool for XML/HTTP enrichment so rx workers are never blocked.
        _xml_workers = 6 if sys.platform == "win32" else 3
        self._xml_enrich_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=_xml_workers, thread_name_prefix="ssdp-xml"
        )

    def _should_skip_recent_duplicate_ssdp(self, headers: dict[str, str], src_ip: str) -> bool:
        loc = (headers.get("LOCATION") or "").strip()
        usn = (headers.get("USN") or "").strip()
        if not loc or not usn:
            return False
        key = f"{src_ip}\0{usn}\0{loc}"
        now = time.monotonic()
        with self._rx_dedup_lock:
            if len(self._rx_dedup) > _RX_DEDUP_MAX_KEYS:
                cutoff = now - _RX_DEDUP_PRUNE_AGE_S
                self._rx_dedup = {k: v for k, v in self._rx_dedup.items() if v >= cutoff}
            last = self._rx_dedup.get(key)
            if last is not None and (now - last) < _RX_DEDUP_WINDOW_S:
                return True
            self._rx_dedup[key] = now
        return False

    def _directed_msearch_targets(self) -> list[str]:
        configured = set(self._msearch_directed_ips)
        extra: set[str] = set()
        if self._ephemeral_probe_resolver is not None:
            try:
                for raw in self._ephemeral_probe_resolver():
                    if not isinstance(raw, str) or not raw.strip():
                        continue
                    try:
                        addr = ipaddress.ip_address(raw.strip())
                    except ValueError:
                        continue
                    if isinstance(addr, ipaddress.IPv4Address) and not addr.is_loopback:
                        extra.add(addr.compressed)
            except Exception:
                self._logger.debug("ephemeral SSDP M-SEARCH target resolver failed", exc_info=True)
        return sorted(configured | extra)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        # Detect interfaces once so both sockets use the same list.
        if_addrs = _local_ipv4_multicast_ifaces()
        self._multicast_if_ipv4s = list(if_addrs)
        if self._multicast_if_ipv4s:
            self._logger.info(
                "SSDP multicast will pin to IPv4 interface(s): %s",
                ", ".join(self._multicast_if_ipv4s),
            )
        else:
            self._logger.info("SSDP multicast: no usable local IPv4 list; using OS default interface only")
        # Dual socket architecture:
        #   _send_socket — ephemeral port; sends M-SEARCH and receives unicast responses.
        #     On Windows, port 1900 responses go to the Windows SSDP service; an ephemeral
        #     source port means M-SEARCH replies come back to US, not to that service.
        #   _recv_socket — port 1900; receives multicast NOTIFY announcements.
        self._send_socket = self._create_send_socket(if_addrs)
        self._recv_socket = self._create_recv_socket(if_addrs)
        self._logger.info("SSDP discovery started")
        self._listen_thread = threading.Thread(target=self._listen_loop, name="ssdp-listener", daemon=True)
        self._listen_thread.start()
        self._rx_worker_threads = []
        for i in range(_RX_WORKER_COUNT):
            wt = threading.Thread(target=self._rx_worker_loop, name=f"ssdp-rx-worker-{i}", daemon=True)
            self._rx_worker_threads.append(wt)
            wt.start()
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
        for attr in ("_send_socket", "_recv_socket"):
            sock = getattr(self, attr, None)
            setattr(self, attr, None)
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        if self._listen_thread is not None:
            self._listen_thread.join(timeout=0.25)
        for wt in self._rx_worker_threads:
            wt.join(timeout=1.5)
        self._rx_worker_threads.clear()
        if self._gc_thread is not None:
            self._gc_thread.join(timeout=0.25)
        if self._refresh_thread is not None:
            self._refresh_thread.join(timeout=0.25)
        self._xml_enrich_executor.shutdown(wait=False, cancel_futures=True)
        self._flush_persistent_caches_if_due(force=True)
        self._logger.info("SSDP discovery stopped")

    def refresh(self) -> None:
        if not self._running:
            return
        sock = self._send_socket
        if sock is None:
            return
        search_targets = _SSDP_SEARCH_ST
        mcast_ifs = self._multicast_if_ipv4s
        mcast_round_robin = mcast_ifs if mcast_ifs else [None]
        for st in search_targets:
            payload = (
                "M-SEARCH * HTTP/1.1\r\n"
                "HOST: 239.255.255.250:1900\r\n"
                "MAN: \"ssdp:discover\"\r\n"
                f"MX: {self._mx_seconds}\r\n"
                f"ST: {st}\r\n"
                "\r\n"
            ).encode("utf-8")
            payload_text = payload.decode("utf-8", errors="ignore")
            for if_ip in mcast_round_robin:
                if if_ip is not None:
                    try:
                        sock.setsockopt(
                            socket.IPPROTO_IP,
                            socket.IP_MULTICAST_IF,
                            socket.inet_aton(if_ip),
                        )
                    except OSError:
                        self._logger.debug("IP_MULTICAST_IF failed for %s", if_ip, exc_info=True)
                        continue
                try:
                    sock.sendto(payload, _SSDP_ADDR)
                    self._logger.debug(
                        "SSDP TX %s:%s if=%s\n%s\n%s\n%s",
                        _SSDP_ADDR[0],
                        _SSDP_ADDR[1],
                        if_ip or "default",
                        _TX_FRAME_DELIMITER,
                        payload_text,
                        _TX_FRAME_DELIMITER,
                    )
                except OSError:
                    self._logger.exception("Failed to send SSDP M-SEARCH for ST=%s (if=%s)", st, if_ip)
        for dip in self._directed_msearch_targets():
            host_line = f"{dip}:1900"
            for st in search_targets:
                payload = (
                    "M-SEARCH * HTTP/1.1\r\n"
                    f"HOST: {host_line}\r\n"
                    "MAN: \"ssdp:discover\"\r\n"
                    f"MX: {self._mx_seconds}\r\n"
                    f"ST: {st}\r\n"
                    "\r\n"
                ).encode("utf-8")
                try:
                    sock.sendto(payload, (dip, 1900))
                    self._logger.debug(
                        "SSDP directed TX to %s ST=%s\n%s\n%s\n%s",
                        host_line,
                        st,
                        _TX_FRAME_DELIMITER,
                        payload.decode("utf-8", errors="ignore"),
                        _TX_FRAME_DELIMITER,
                    )
                except OSError:
                    self._logger.exception(
                        "Failed to send directed SSDP M-SEARCH to %s ST=%s",
                        host_line,
                        st,
                    )

    def _create_send_socket(self, if_addrs: list[str]) -> socket.socket:
        """Ephemeral-port socket for M-SEARCH sending and unicast response receiving.

        Because it is not bound to port 1900, M-SEARCH replies (unicast UDP back to the
        sender's source port) arrive here instead of being intercepted by the Windows SSDP
        service, which wins the port-1900 delivery race on shared sockets.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.2)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("B", 4))
        except OSError:
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
            except OSError:
                self._logger.debug("Could not set IP_MULTICAST_TTL on send socket", exc_info=True)
        sock.bind(("", 0))
        port = sock.getsockname()[1]
        self._logger.info(
            "SSDP send socket bound to ephemeral port %d (unicast M-SEARCH responses will arrive here)",
            port,
        )
        return sock

    def _create_recv_socket(self, if_addrs: list[str]) -> socket.socket:
        """Port-1900 socket for multicast NOTIFY announcements.

        Bound to port 1900 with SO_REUSEADDR so it shares the port with the Windows SSDP
        service.  It receives multicast NOTIFY traffic (devices advertising themselves
        spontaneously) but NOT the unicast M-SEARCH replies — those go to ``_send_socket``.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # SO_REUSEPORT is not defined on Windows; optional on POSIX.
        if sys.platform != "win32" and hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        sock.settimeout(0.2)
        # Large receive buffer to absorb multicast NOTIFY bursts.
        _rcvbuf = 512 * 1024 if sys.platform == "win32" else 256 * 1024
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, _rcvbuf)
        except OSError:
            pass
        try:
            sock.bind(("", 1900))
            self._logger.info("SSDP recv socket bound to 0.0.0.0:1900 (multicast NOTIFY receive)")
        except OSError as exc:
            _bound_mcast = False
            if sys.platform == "win32":
                # Fallback: bind to the multicast group address itself — still receives NOTIFY.
                try:
                    sock.bind((_SSDP_ADDR[0], 1900))
                    _bound_mcast = True
                    self._logger.info(
                        "SSDP recv socket bound to %s:1900 (Windows port-sharing fallback; NOTIFY receive only)",
                        _SSDP_ADDR[0],
                    )
                except OSError:
                    pass
            if not _bound_mcast:
                self._logger.warning(
                    "SSDP recv socket could not bind UDP port 1900 (%s). "
                    "Multicast NOTIFY may be missed. "
                    "Unicast M-SEARCH responses will still arrive on the send socket.",
                    exc,
                )
                sock.bind(("", 0))
        mcast_bin = socket.inet_aton(_SSDP_ADDR[0])
        joined_any = False
        for if_ip in if_addrs:
            try:
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_ADD_MEMBERSHIP,
                    mcast_bin + socket.inet_aton(if_ip),
                )
                joined_any = True
            except OSError:
                self._logger.debug("IP_ADD_MEMBERSHIP failed for %s on recv socket", if_ip, exc_info=True)
        if not joined_any:
            try:
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_ADD_MEMBERSHIP,
                    mcast_bin + socket.inet_aton("0.0.0.0"),
                )
            except OSError:
                self._logger.debug("Unable to join SSDP multicast group (INADDR_ANY) on recv socket", exc_info=True)
        return sock

    def _listen_loop(self) -> None:
        while self._running:
            send_sock = self._send_socket
            recv_sock = self._recv_socket
            readable_pool = [s for s in (send_sock, recv_sock) if s is not None]
            if not readable_pool:
                break
            try:
                ready, _, _ = select.select(readable_pool, [], [], 0.2)
            except OSError:
                break
            for sock in ready:
                try:
                    data, addr = sock.recvfrom(65535)
                except OSError:
                    continue
                raw = data.decode("utf-8", errors="ignore")
                try:
                    self._rx_queue.put_nowait((raw, addr[0]))
                except queue.Full:
                    self._logger.warning(
                        "SSDP RX queue full (%d); dropping packet from %s — workers cannot keep up",
                        _RX_QUEUE_MAXSIZE,
                        addr[0],
                    )

    def _rx_worker_loop(self) -> None:
        while self._running:
            try:
                payload, src_ip = self._rx_queue.get(timeout=0.3)
            except queue.Empty:
                continue
            # Accept both HTTP/1.1 and HTTP/1.0 — some devices (older TVs, embedded firmware) use 1.0.
            # Case-insensitive match on the first token — some devices send lowercase.
            first = payload[:20].upper()
            is_reply = first.startswith("HTTP/1.1 200") or first.startswith("HTTP/1.0 200")
            is_notify = first.startswith("NOTIFY * HTTP/1")
            if not (is_reply or is_notify):
                self._logger.debug(
                    "SSDP RX ignored (unrecognized type) from %s: %.80s",
                    src_ip,
                    payload[:80].replace("\r\n", " | "),
                )
                continue
            self._logger.debug(
                "SSDP RX from %s\n%s\n%s\n%s",
                src_ip,
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
                    offline_payload = self._build_notify_offline_payload(headers, src_ip)
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
            if self._should_skip_recent_duplicate_ssdp(headers, src_ip):
                continue
            # Emit immediately from headers (no HTTP round-trip) so the device appears fast.
            device_payload = self._build_device_payload(headers, src_ip, fetch_xml=False)
            self._logger.debug(
                "SSDP response from %s -> %s %s:%s (fast emit, XML pending)",
                src_ip,
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
            # Offload XML/HTTP fetch to the enrichment pool so rx workers stay free.
            if headers.get("LOCATION"):
                self._xml_enrich_executor.submit(self._xml_enrich_task, headers, src_ip)

    def _xml_enrich_task(self, headers: dict[str, str], src_ip: str) -> None:
        """Fetch XML descriptor and re-emit the device with enriched data."""
        try:
            enriched = self._build_device_payload(headers, src_ip, fetch_xml=True)
            device_key = self._device_key(enriched)
            now = datetime.now(timezone.utc)
            timeout_seconds = self._timeout_seconds_for_payload(enriched)
            with self._lock:
                self._seen_devices[device_key] = (now, timeout_seconds, enriched)
            self._emit("device", enriched)
            self._logger.debug(
                "SSDP XML enriched %s -> %s %s:%s",
                src_ip,
                enriched.get("name"),
                enriched.get("ip"),
                enriched.get("port"),
            )
        except Exception:
            self._logger.debug("SSDP XML enrichment failed for %s", src_ip, exc_info=True)

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

    def _build_device_payload(self, headers: dict[str, str], fallback_ip: str, *, fetch_xml: bool = True) -> dict:
        location = headers.get("LOCATION")
        parsed = urlparse(location) if location else None
        ip = _resolve_ssdp_device_ip(parsed.hostname if parsed else None, fallback_ip)
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
        if fetch_xml:
            xml_fields, raw_xml = self._fetch_and_parse_xml(location)
            dev_loc = _sonos_device_description_alternate_url(location)
            if dev_loc and dev_loc != (location or "").strip():
                xf_dev, raw_dev = self._fetch_and_parse_xml(
                    dev_loc,
                    bypass_descriptor_min_interval=True,
                )
                if xf_dev:
                    for key in (
                        "friendlyName",
                        "displayName",
                        "roomName",
                        "modelName",
                        "manufacturer",
                        "manufacturerURL",
                        "modelURL",
                        "serialNumber",
                        "deviceType",
                        "UDN",
                        "mac",
                        "modelType",
                        "presentationURL",
                        "iconURL",
                        "icons_description",
                        "services_description",
                        "services_records",
                    ):
                        val = xf_dev.get(key)
                        if val:
                            xml_fields[key] = val
                    if raw_dev:
                        raw_xml = raw_dev
        else:
            xml_fields, raw_xml = {}, None
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
        # Only persist profile when we have real XML data to avoid overwriting rich cache with stubs.
        if fetch_xml:
            self._persist_profile_cache_entries(headers, ip, payload)
        self._logger.info(
            "SSDP device detected: %s %s:%s type=%s category=%s st=%s nt=%s (xml=%s)",
            payload["name"],
            payload["ip"],
            payload["port"],
            payload["type"],
            payload["category"],
            st,
            nt,
            fetch_xml,
        )
        return payload

    def _build_notify_offline_payload(self, headers: dict[str, str], fallback_ip: str) -> dict:
        st = headers.get("NT") or headers.get("ST") or "ssdp:all"
        usn = headers.get("USN", "")
        server = headers.get("SERVER", "")
        location = headers.get("LOCATION")
        parsed = urlparse(location) if location else None
        ip = _resolve_ssdp_device_ip(parsed.hostname if parsed else None, fallback_ip)
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

        usn = metadata.get("usn")
        if isinstance(usn, str) and usn.strip():
            usn_base = usn.strip().lower().split("::", 1)[0]
            if usn_base:
                return f"ssdp:usn:{usn_base}"

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

    def _fetch_and_parse_xml(
        self,
        location: str | None,
        *,
        bypass_descriptor_min_interval: bool = False,
    ) -> tuple[dict, str | None]:
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
            if (
                not bypass_descriptor_min_interval
                and self._descriptor_http_min_interval > 0
                and host_key
            ):
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
        usn_persist = metadata.get("usn") if isinstance(metadata.get("usn"), str) else ""
        if usn_persist.strip():
            row["usn"] = usn_persist.strip()
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
