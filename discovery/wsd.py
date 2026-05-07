# File wsd.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""WS-Discovery (WSD) — multicast SOAP/UDP discovery for Windows and compatible hosts."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import threading
import time
from urllib.parse import urlparse

from discovery.base import BaseDiscovery

try:
    from wsdiscovery import QName
except ImportError:  # pragma: no cover - optional dependency
    QName = None  # type: ignore[misc, assignment]

try:
    from wsdiscovery.discovery import ThreadedWSDiscovery as _ThreadedWSDiscovery
except ImportError:  # pragma: no cover - optional dependency
    _ThreadedWSDiscovery = None

_DEFAULT_INTERVAL_S = 90.0
_DEFAULT_TIMEOUT_S = 8.0
# Remember last LAN IPv4 per WSD EPR when a later probe only lists link-local IPv6 (common on Wi‑Fi mini PCs).
_STICKY_LAN_IPV4_TTL_S = 3600.0

# DPWS "Device" — many Windows and embedded hosts match this type in Probe.
_DPWS_DEVICE_TYPE: object | None
if QName is not None:
    _DPWS_DEVICE_TYPE = QName("http://schemas.xmlsoap.org/ws/2006/02/devprof", "Device")
else:
    _DPWS_DEVICE_TYPE = None

# soap.udp://[fe80::1]:3702/ or soap.udp://192.168.1.1:3702/…
_SOAP_UDP_RE = re.compile(
    r"^soap\.udp://(?:\[([0-9a-fA-F:%]+)\]|([^]:/]+)):([0-9]+)",
    re.IGNORECASE,
)
# Scope tail that is only a UUID is not a friendly display name.
_SCOPE_UUID_TAIL = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    re.IGNORECASE,
)
_EPR_UUID_RE = re.compile(
    r"(?:urn:uuid:|uuid:)([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
    re.IGNORECASE,
)
# Windows sometimes exposes scopes like ``WSD-45982f41`` — not a computer name.
_SYNTH_WSD_SCOPE_LABEL = re.compile(
    r"^ws[d]?[\s\-·∙]+[0-9a-f]{6,}$",
    re.IGNORECASE,
)


def _is_unfriendly_wsd_scope_label(text: str) -> bool:
    """True for UUID-only tails or synthetic ``WSD-…`` browse ids from scopes."""
    s = (text or "").strip()
    if not s:
        return True
    if _SCOPE_UUID_TAIL.match(s):
        return True
    if _SYNTH_WSD_SCOPE_LABEL.match(s):
        return True
    return False


# Display strings like ``WSD ·45982f41`` / ``ws-xxxxx`` — not hostnames (cf. ui/device_list weak names).
_WSD_SYNTHETIC_DISPLAY_RE = re.compile(
    r"^ws[d]?[\s\-·∙]+[0-9a-f]{6,}$",
    re.IGNORECASE,
)


def is_synthetic_wsd_display_name(name: str) -> bool:
    """True if the WSD row label is a fallback rather than a hostname (scope for NetBIOS assist)."""
    n = (name or "").strip()
    if not n:
        return True
    low = n.lower()
    if low == "wsd host":
        return True
    if low.startswith("wsd ·") or low.startswith("wsd \u00b7"):
        return True
    if _WSD_SYNTHETIC_DISPLAY_RE.match(n):
        return True
    return False


def _category_for_wsd_type(device_type: str) -> str:
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


def _qname_suggests_computer(t) -> bool:
    """DPWS ``Device`` / devprof — Windows PCs often include this alongside print stack types."""
    if _DPWS_DEVICE_TYPE is not None:
        try:
            if t == _DPWS_DEVICE_TYPE:
                return True
        except Exception:
            pass
    try:
        ns = (t.getNamespace() or "").lower()
        local = (t.getLocalname() or "").lower()
    except Exception:
        return False
    if local == "device" and "devprof" in ns:
        return True
    # Host / workstation hints (avoid treating mixed printer+host ads as printer-only).
    if local in {"computer", "computerdevice"}:
        return True
    return False


def _qname_suggests_printer(t) -> bool:
    """Strong printer/MFP QName signals — same rules as before, but evaluated per-type for aggregation."""
    try:
        ns = (t.getNamespace() or "").lower()
        local = (t.getLocalname() or "").lower()
    except Exception:
        return False
    if local in {"printdevice", "printer"}:
        return True
    if local.endswith("printdevice") or local.endswith("printer"):
        return True
    if "printdevice" in ns:
        return True
    if "/wdp/" in ns and local and any(
        x in local for x in ("print", "printer", "printdevice", "fax")
    ):
        return True
    if "microsoft.com/windows" in ns and local and any(
        x in local for x in ("printdevice", "printer", "fax")
    ):
        return True
    if local in {"scanner", "fax", "faxdevice"} or local.endswith("scannerdevice"):
        return True
    return False


def _infer_type_from_qnames(types) -> str:
    """Classify service type. Windows often lists print QNames and DPWS ``Device`` together — prefer PC."""
    if not types:
        return "computer"
    any_pc = False
    any_printer = False
    for t in types:
        if _qname_suggests_computer(t):
            any_pc = True
        if _qname_suggests_printer(t):
            any_printer = True
    if any_pc:
        return "computer"
    if any_printer:
        return "networkprinter"
    return "computer"


def _reverse_dns_short_name(ip_s: str) -> str | None:
    """First label from PTR / ``gethostbyaddr`` — often empty on home LANs, cheap when it works."""
    try:
        addr = ipaddress.ip_address(ip_s)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.is_link_local:
        return None
    try:
        host, _aliases, _ips = socket.gethostbyaddr(ip_s)
    except OSError:
        return None
    first = (host or "").strip().split(".")[0]
    if not first or len(first) > 63 or not first.isprintable():
        return None
    if first.lower() == (ip_s or "").lower():
        return None
    return first


def _short_name_from_epr(epr: str) -> str | None:
    """Distinguish anonymous WSD rows when scopes/xaddrs carry no label (not a real hostname)."""
    m = _EPR_UUID_RE.search(epr or "")
    if not m:
        return None
    digits = m.group(1).replace("-", "")
    tail = digits[-8:] if len(digits) >= 8 else digits
    return f"WSD ·{tail}"


def _friendly_hostnames_from_xaddrs(xaddrs: list[str]) -> list[str]:
    """Hostnames embedded in http(s) xaddrs (not literal IPs) — often match Windows computer names."""
    found: list[str] = []
    for xa in xaddrs:
        if not xa or not isinstance(xa, str):
            continue
        low = xa.lower().strip()
        if low.startswith(("soap.", "urn:", "uuid:")):
            continue
        try:
            parsed = urlparse(xa.strip())
        except ValueError:
            continue
        h = (parsed.hostname or "").strip("[]")
        if not h or len(h) > 120:
            continue
        try:
            ipaddress.ip_address(h)
        except ValueError:
            if h.isprintable():
                found.append(h)
    return found


def _display_name_for_service(svc, xaddrs: list[str], preferred_ip: str, epr: str) -> str:
    for sc in svc.getScopes() or []:
        try:
            text = str(sc).strip()
        except Exception:
            continue
        if text and len(text) < 200:
            if "/" not in text[:20] and not text.startswith("ldap:"):
                if not _is_unfriendly_wsd_scope_label(text):
                    return text
                continue
            tail = text.rstrip("/").split("/")[-1]
            if tail and tail.isprintable() and len(tail) < 80:
                if _is_unfriendly_wsd_scope_label(tail):
                    continue
                return tail
    for hn in _friendly_hostnames_from_xaddrs(xaddrs):
        low = hn.lower()
        if low not in {"localhost", "host", "device"}:
            return hn
    ptr = _reverse_dns_short_name(preferred_ip)
    if ptr:
        return ptr
    ep = _short_name_from_epr(epr)
    if ep:
        return ep
    return "WSD host"


def _endpoint_address_rank(ip_s: str) -> tuple[int, int]:
    """Prefer LAN IPv4 over IPv6 link-local (``fe80::``) when multiple xaddrs exist."""
    try:
        a = ipaddress.ip_address(ip_s)
    except ValueError:
        return (99, 0)
    if isinstance(a, ipaddress.IPv4Address):
        if a.is_loopback:
            return (5, 0)
        if a.is_private:
            return (0, 0)
        if a.is_link_local:
            return (3, 0)
        return (1, 0)
    if isinstance(a, ipaddress.IPv6Address):
        if a.is_loopback:
            return (6, 0)
        if a.is_link_local:
            return (4, 0)
        if a.is_private:
            return (2, 0)
        return (2, 0)
    return (99, 0)


def _endpoint_candidate_from_xaddr(xa: str) -> tuple[str, int, str | None] | None:
    """Single xaddr → ``(ip, port, http_url_or_none)`` if a usable address is found."""
    if not xa or not isinstance(xa, str):
        return None
    if "///" in xa:
        return None
    low = xa.lower()
    if low.startswith("soap.udp") or low.startswith("soap.tcp"):
        sudp = _parse_soap_udp_host_port(xa)
        if not sudp:
            return None
        host_raw, p_soap = sudp
        try:
            ipaddress.ip_address(host_raw)
            cand_ip = host_raw
        except ValueError:
            cand_ip = _resolve_host_to_ip(host_raw) or ""
        if not cand_ip or cand_ip in {"127.0.0.1", "::1"}:
            return None
        return cand_ip, int(p_soap), None
    if low.startswith("soap."):
        return None
    host, p, u = _parse_endpoint_ip_port_url(xa)
    if host and host not in {"127.0.0.1", "::1"}:
        return host, int(p), u
    return None


def _pick_best_endpoint(xaddrs: list[str]) -> tuple[str, int, str | None]:
    """Among all xaddrs, pick the best IP for display (IPv4 LAN before ``fe80::``)."""
    cands: list[tuple[str, int, str | None]] = []
    seen: set[tuple[str, int]] = set()
    for xa in xaddrs:
        c = _endpoint_candidate_from_xaddr(xa)
        if not c:
            continue
        ip_s, port, url = c
        key = (ip_s, port)
        if key in seen:
            continue
        seen.add(key)
        cands.append((ip_s, port, url))

    if not cands:
        return "", 445, None

    def _sort_key(item: tuple[str, int, str | None]) -> tuple[tuple[int, int], int, int]:
        ip_s, _port, _u = item
        try:
            a = ipaddress.ip_address(ip_s)
            v6 = 1 if isinstance(a, ipaddress.IPv6Address) else 0
        except ValueError:
            v6 = 9
        http_bonus = 0 if item[2] else 1
        return (_endpoint_address_rank(ip_s), v6, http_bonus)

    best = min(cands, key=_sort_key)
    return best[0], best[1], best[2]


def _parse_soap_udp_host_port(xa: str) -> tuple[str, int] | None:
    m = _SOAP_UDP_RE.match((xa or "").strip())
    if not m:
        return None
    v6, v4, port_s = m.group(1), m.group(2), m.group(3)
    host = (v6 or v4 or "").strip()
    if not host:
        return None
    try:
        return host, int(port_s)
    except ValueError:
        return None


def _resolve_host_to_ip(host: str) -> str | None:
    """Best-effort LAN resolution for WSD xaddrs that use a hostname / NetBIOS label."""
    h = (host or "").strip()
    if not h:
        return None
    try:
        ipaddress.ip_address(h)
        return h
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(h, None, type=socket.SOCK_STREAM)
    except OSError:
        return None
    v4 = [i for i in infos if i[0] == socket.AF_INET]
    v6 = [i for i in infos if i[0] == socket.AF_INET6]
    for lst in (v4, v6):
        for item in lst:
            addr = item[4][0]
            if addr in {"127.0.0.1", "::1"}:
                continue
            try:
                ipaddress.ip_address(addr)
            except ValueError:
                continue
            return addr
    return None


def _parse_endpoint_ip_port_url(http_url: str | None) -> tuple[str, int, str | None]:
    """Return (ip_str, port, url_or_none). ``ip_str`` is a literal address or resolved hostname."""
    if not http_url or not isinstance(http_url, str):
        return "", 0, None
    raw = http_url.strip()
    if not raw or "://" not in raw:
        return "", 0, None
    low = raw.lower()
    if low.startswith("soap."):
        return "", 0, None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return "", 0, None
    host = parsed.hostname
    if not host:
        return "", 0, None
    port = parsed.port
    if port is None:
        if (parsed.scheme or "").lower() == "https":
            port = 443
        else:
            port = 80
    chost = host.strip("[]")
    try:
        ipaddress.ip_address(chost)
        return chost, int(port), raw
    except ValueError:
        pass
    ip_lit = _resolve_host_to_ip(chost)
    if ip_lit:
        return ip_lit, int(port), raw
    return "", 0, raw


def _pick_addresses(svc) -> list[str]:
    try:
        xs = svc.getXAddrs()
    except Exception:
        return []
    if not xs:
        return []
    out: list[str] = []
    for x in xs:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


class WSDiscovery(BaseDiscovery):
    """Periodic WS-Discovery probe; emits ``computer`` / ``networkprinter`` style devices."""

    def __init__(
        self,
        *,
        interval_seconds: float | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(source="wsd")
        self._logger = logging.getLogger(__name__)
        self._interval_s = (
            float(interval_seconds) if interval_seconds is not None else _DEFAULT_INTERVAL_S
        )
        self._timeout_s = (
            float(timeout_seconds) if timeout_seconds is not None else _DEFAULT_TIMEOUT_S
        )
        self._interval_s = max(15.0, self._interval_s)
        self._timeout_s = max(3.0, min(60.0, self._timeout_s))
        self._epr_lan_ipv4: dict[str, tuple[str, float]] = {}
        self._running = False
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._probe_thread: threading.Thread | None = None
        self._engine: object | None = None

    def start(self) -> None:
        if self._running:
            return
        if _ThreadedWSDiscovery is None:
            self._logger.warning("WSD discovery unavailable: install package WSDiscovery (PyPI)")
            return
        self._running = True
        self._stop_event.clear()
        self._wake_event.clear()
        try:
            self._engine = _ThreadedWSDiscovery()
            self._engine.start()
        except Exception:
            self._logger.exception("WSD ThreadedWSDiscovery failed to start")
            self._running = False
            self._engine = None
            return
        self._probe_thread = threading.Thread(target=self._probe_loop, name="wsd-probe", daemon=True)
        self._probe_thread.start()
        self._logger.info("WSD discovery started (interval=%.1fs timeout=%.1fs)", self._interval_s, self._timeout_s)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        self._wake_event.set()
        eng = self._engine
        self._engine = None
        if eng is not None:
            try:
                eng.stop()
            except Exception:
                self._logger.debug("WSD engine stop raised", exc_info=True)
        if self._probe_thread is not None:
            self._probe_thread.join(timeout=1.0)
            self._probe_thread = None
        self._logger.info("WSD discovery stopped")

    def refresh(self) -> None:
        if not self._running:
            return
        self._wake_event.set()

    def _probe_loop(self) -> None:
        while self._running:
            self._run_once()
            if self._stop_event.is_set():
                break
            if self._wake_event.wait(timeout=self._interval_s):
                self._wake_event.clear()

    def _run_once(self) -> None:
        eng = self._engine
        if eng is None or not self._running:
            return
        try:
            # Do not call clearRemoteServices() each cycle — Windows hosts often reply slower or skip a
            # probe entirely; wiping _remoteServices would drop the PC from the merged set while leaving
            # faster responders (e.g. printers) as the sole entry until the next lucky reply window.
            # Typed DPWS probe first, broad probe last: ThreadedWSDiscovery stores one ``Service`` per
            # EPR — a later ProbeMatch overwrites the earlier. A narrow typed match can carry thinner
            # metadata than a broad ProbeMatch; running broad last keeps fuller ``Types`` / ``XAddrs``.
            broad_s = max(8.0, float(self._timeout_s))
            if _DPWS_DEVICE_TYPE is not None:
                try:
                    eng.searchServices(types=[_DPWS_DEVICE_TYPE], timeout=min(8.0, broad_s))
                except Exception:
                    self._logger.debug("WSD DPWS-typed probe failed", exc_info=True)
            try:
                eng.searchServices(timeout=broad_s)
            except Exception:
                self._logger.debug("WSD broad searchServices failed", exc_info=True)
                return
            # Second broad sweep — some stacks (esp. mobile / Wi‑Fi) answer after the first window.
            try:
                eng.searchServices(timeout=min(5.0, max(3.0, broad_s * 0.45)))
            except Exception:
                self._logger.debug("WSD second broad searchServices failed", exc_info=True)
            # ThreadedWSDiscovery keeps matches in _remoteServices (no public accessor).
            try:
                services = list(getattr(eng, "_remoteServices", {}).values())
            except Exception:
                services = []
        except Exception:
            self._logger.debug("WSD probe sequence failed", exc_info=True)
            return
        self._prune_sticky_epr_map()
        if not services:
            self._logger.debug(
                "WSD: no responses (firewall may block UDP 3702; many Windows PCs do not expose a "
                "useful WSD endpoint — NetBIOS browse may still be required)"
            )
            return
        self._logger.debug("WSD: combined service count=%d", len(services))
        for svc in services:
            try:
                payload = self._service_to_payload(svc)
                if payload:
                    self._emit("device", payload)
                else:
                    self._log_skip_reason(svc, "no usable LAN IP")
            except Exception:
                self._logger.debug("WSD service→payload failed", exc_info=True)

    def _prune_sticky_epr_map(self) -> None:
        now = time.monotonic()
        ttl = _STICKY_LAN_IPV4_TTL_S
        self._epr_lan_ipv4 = {k: v for k, v in self._epr_lan_ipv4.items() if now - v[1] <= ttl}

    def _sticky_lan_ipv4_for_epr(
        self,
        epr: str,
        ip_s: str,
        port: int,
        url: str | None,
        xaddrs: list[str],
    ) -> tuple[str, int, str | None]:
        """If this probe only lists ``fe80::…`` but we recently saw a LAN IPv4 for the same EPR, reuse it."""
        key = (epr or "").strip().lower()
        if not key:
            return ip_s, port, url
        now = time.monotonic()

        for xa in xaddrs:
            c = _endpoint_candidate_from_xaddr(xa)
            if not c:
                continue
            sip, _, _ = c
            try:
                a = ipaddress.ip_address(sip)
            except ValueError:
                continue
            if isinstance(a, ipaddress.IPv4Address) and not a.is_loopback:
                self._epr_lan_ipv4[key] = (sip, now)
                break

        try:
            cur = ipaddress.ip_address(ip_s)
        except ValueError:
            return ip_s, port, url

        if (
            isinstance(cur, ipaddress.IPv6Address)
            and cur.is_link_local
            and key in self._epr_lan_ipv4
        ):
            rem, t0 = self._epr_lan_ipv4[key]
            if now - t0 <= _STICKY_LAN_IPV4_TTL_S:
                try:
                    raddr = ipaddress.ip_address(rem)
                    if isinstance(raddr, ipaddress.IPv4Address):
                        self._logger.debug("WSD sticky LAN IPv4 %s (cycle had %s)", rem, ip_s)
                        return rem, port, url
                except ValueError:
                    pass
        return ip_s, port, url

    def _log_skip_reason(self, svc, reason: str = "no usable LAN IP") -> None:
        try:
            epr = (svc.getEPR() or "")[:80]
            xs = _pick_addresses(svc)
            self._logger.debug(
                "WSD skip (%s): epr=%r xaddrs_sample=%r",
                reason,
                epr,
                xs[:3] if xs else xs,
            )
        except Exception:
            pass

    def _service_to_payload(self, svc) -> dict | None:
        epr = ""
        try:
            epr = (svc.getEPR() or "").strip()
        except Exception:
            pass
        if not epr:
            return None
        dev_type = _infer_type_from_qnames(svc.getTypes())
        if dev_type != "computer":
            # Windows often advertises only print QNames; still surface the host (category may be Printers).
            self._logger.debug(
                "WSD non-PC QName stack (listing anyway): inferred_type=%s epr=%.60r",
                dev_type,
                epr,
            )
        xaddrs = _pick_addresses(svc)
        ip_s, port, url = _pick_best_endpoint(xaddrs)
        ip_s, port, url = self._sticky_lan_ipv4_for_epr(epr, ip_s, port, url, xaddrs)
        if not ip_s or ip_s in {"127.0.0.1", "::1"}:
            return None
        if dev_type == "computer" and port in (80, 443) and not url:
            port = 445
        name = _display_name_for_service(svc, xaddrs, ip_s, epr)
        type_qnames: list[str] = []
        for t in svc.getTypes() or []:
            try:
                type_qnames.append(f"{t.getNamespace() or ''}|{t.getLocalname() or ''}")
            except Exception:
                continue
        scope_strs: list[str] = []
        for sc in svc.getScopes() or []:
            try:
                scope_strs.append(str(sc))
            except Exception:
                continue
        metadata = {
            "wsd_epr": epr,
            "wsd_xaddrs": xaddrs,
            "wsd_types": type_qnames,
            "wsd_scopes": scope_strs,
        }
        return {
            "name": name,
            "ip": ip_s,
            "port": int(port),
            "type": dev_type,
            "category": _category_for_wsd_type(dev_type),
            "source": self.source,
            "url": url,
            "metadata": metadata,
            "online": True,
            "icon": None,
        }
