# File netbios.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""NetBIOS LAN discovery via Samba ``nmblookup`` (NMB broadcast / name query)."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import logging
import re
import shutil
import subprocess
import sys
import threading
from collections import OrderedDict, defaultdict
from collections.abc import Sequence

from discovery.base import BaseDiscovery

_DEFAULT_INTERVAL_S = 180.0
_DEFAULT_TIMEOUT_S = 15.0


def _subprocess_no_window_kwargs() -> dict[str, object]:
    """Avoid flashing console windows when ``nmblookup`` runs on Windows."""
    if sys.platform != "win32":
        return {}
    out: dict[str, object] = {}
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if flag:
        out["creationflags"] = flag
    # Some CUI builds still create a brief host window; STARTUPINFO reinforces hide.
    if hasattr(subprocess, "STARTUPINFO"):
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        out["startupinfo"] = si
    return out


_NMB_SUBPROCESS_KW = _subprocess_no_window_kwargs()

# ``Looking up status of 192.168.1.10`` / IPv6
_STATUS_OF_RE = re.compile(r"^Looking up status of\s+(\S+)\s*$", re.IGNORECASE)
# ``	DS415PLUS       <00> -         B <ACTIVE>``
_STATUS_NAME_RE = re.compile(r"^\s+(\S+)\s+<([0-9a-fA-F]{2})>\s*-")
# nbtstat -A output: ``   MYPC           <00>  UNIQUE      Registered``
_NBTSTAT_NAME_RE = re.compile(
    r"^\s+(\S+)\s+<([0-9a-fA-F]{2})>\s+(?:UNIQUE|GROUP)\b",
    re.IGNORECASE,
)


def _parse_ip_name_suffix(rest: str) -> tuple[str, str] | None:
    """Parse ``HOSTNAME<00>`` tail after first column."""
    m = re.match(r"^(.+)<([0-9a-fA-F]{2})>\s*$", rest.strip())
    if not m:
        return None
    return m.group(1).strip(), m.group(2).lower()


def _parse_flat_ip_line(line: str) -> tuple[str, str, str] | None:
    """Parse ``192.168.1.10 HOST<00>`` or IPv6 equivalent."""
    line = line.strip()
    if not line or line.lower().startswith("querying"):
        return None
    parts = line.split(None, 1)
    if len(parts) != 2:
        return None
    ip_s, rest = parts[0], parts[1].strip()
    try:
        addr = ipaddress.ip_address(ip_s)
    except ValueError:
        return None
    parsed = _parse_ip_name_suffix(rest)
    if not parsed:
        return None
    name, suf = parsed
    return str(addr), name, suf


def _ip_rank(ip_s: str) -> tuple[int, int]:
    """Sort key: lower = better. Prefer LAN IPv4 over IPv6 (esp. link-local)."""
    try:
        a = ipaddress.ip_address(ip_s)
    except ValueError:
        return (99, 99)
    if isinstance(a, ipaddress.IPv4Address):
        if a.is_loopback:
            return (5, 0)
        if a.is_private or a.is_link_local:
            return (0, 0)
        return (1, 0)
    if isinstance(a, ipaddress.IPv6Address):
        if a.is_loopback:
            return (6, 0)
        if a.is_link_local:
            return (4, 0)
        if a.is_private:
            return (3, 0)
        return (2, 0)
    return (99, 99)


def _is_placeholder_netbios_name(name: str) -> bool:
    n = (name or "").strip()
    return n in {"*", "?"}


def _parse_nbtstat_output(text: str, queried_ip: str) -> list[tuple[str, str, str]]:
    """Parse ``nbtstat -A <IP>`` output → ``(ip, name, suffix_hex)`` rows.

    The "Node IpAddress" header is our local interface IP, not the target —
    use ``queried_ip`` as the canonical IP for all parsed names.
    """
    try:
        ip = str(ipaddress.ip_address(queried_ip.strip()))
    except ValueError:
        ip = queried_ip.strip()
    rows: list[tuple[str, str, str]] = []
    for raw in (text or "").splitlines():
        m = _NBTSTAT_NAME_RE.match(raw)
        if m:
            rows.append((ip, m.group(1).strip(), m.group(2).lower()))
    return rows


def _parse_nmblookup_output(text: str) -> list[tuple[str, str, str]]:
    """Return rows ``(ip, netbios_name, suffix_hex)`` from ``nmblookup`` stdout/stderr."""
    rows: list[tuple[str, str, str]] = []
    current_status_ip: str | None = None
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lower().lstrip().startswith("querying"):
            continue

        m_hdr = _STATUS_OF_RE.match(line.strip())
        if m_hdr:
            ip_raw = m_hdr.group(1).strip()
            if ip_raw.startswith("[") and "]" in ip_raw:
                ip_raw = ip_raw.split("[", 1)[1].split("]", 1)[0].strip()
            try:
                current_status_ip = str(ipaddress.ip_address(ip_raw))
            except ValueError:
                current_status_ip = None
            continue

        m_stat = _STATUS_NAME_RE.match(line)
        if m_stat and current_status_ip:
            name, suf = m_stat.group(1), m_stat.group(2).lower()
            rows.append((current_status_ip, name, suf))
            continue

        flat = _parse_flat_ip_line(line)
        if flat:
            rows.append(flat)
            continue

    return rows


def _noise_netbios_name(name: str, suffix: str) -> bool:
    bu = name.strip().upper()
    su = (suffix or "").strip().lower()
    # Group/workgroup registrations — not a machine unique name (do not hide whole IP if only `<20>` etc. remains).
    if bu == "WORKGROUP" and su in ("00", "1e", "1d"):
        return True
    if "__MSBROWSE__" in bu:
        return True
    if bu.startswith("__MSBROWSE__"):
        return True
    if bu == "MSBROWSE":
        return True
    return False


def _pick_netbios_display_name(candidates: Sequence[tuple[str, str]]) -> str | None:
    """Pick host-style NetBIOS label for one IP; skip workgroup / wildcard."""
    filtered: list[tuple[str, str]] = []
    for base, suf in candidates:
        if _noise_netbios_name(base, suf):
            continue
        if _is_placeholder_netbios_name(base):
            continue
        filtered.append((base.strip(), suf))
    if not filtered:
        return None
    for base, suf in filtered:
        if suf == "00":
            return base
    for base, suf in filtered:
        if suf == "20":
            return base
    return filtered[0][0]


def _merge_row_sort_key(r: tuple[str, str, str]) -> tuple[tuple[int, int], int]:
    ip_s = r[0]
    try:
        a = ipaddress.ip_address(ip_s)
        v6 = 1 if isinstance(a, ipaddress.IPv6Address) else 0
    except ValueError:
        v6 = 9
    return (_ip_rank(ip_s), v6)


def _merge_rows_prefer_ipv4(rows: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """One row per distinct NetBIOS name; prefer IPv4 / routable over link-local IPv6."""
    by_ip: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for ip_s, name, suf in rows:
        by_ip[ip_s].append((name, suf))

    per_name: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for ip_s, cands in by_ip.items():
        display = _pick_netbios_display_name(cands)
        if not display or _is_placeholder_netbios_name(display):
            continue
        suf_pick = ""
        for nb, suf in cands:
            if nb.strip() == display.strip():
                suf_pick = suf
                break
        per_name[display.strip().lower()].append((ip_s, display, suf_pick))

    merged: list[tuple[str, str, str]] = []
    for _k, group in per_name.items():
        merged.append(min(group, key=_merge_row_sort_key))
    return merged


class NetbiosDiscovery(BaseDiscovery):
    """Periodic ``nmblookup`` sweep; emits ``computer`` rows when hosts respond on NMB."""

    def __init__(
        self,
        *,
        interval_seconds: float | None = None,
        timeout_seconds: float | None = None,
        argv: list[str] | None = None,
        directed_ips: list[str] | None = None,
    ) -> None:
        super().__init__(source="nmb")
        self._logger = logging.getLogger(__name__)
        self._interval_s = (
            float(interval_seconds) if interval_seconds is not None else _DEFAULT_INTERVAL_S
        )
        self._timeout_s = (
            float(timeout_seconds) if timeout_seconds is not None else _DEFAULT_TIMEOUT_S
        )
        self._interval_s = max(60.0, min(3600.0, self._interval_s))
        self._timeout_s = max(5.0, min(120.0, self._timeout_s))
        # ``-S`` adds node status so we get real names (plain ``*`` only yields ``*<00>`` per IP).
        self._argv_rest: list[str] = list(argv) if argv else ["-S", "*"]
        self._directed_ips: list[str] = []
        for raw in directed_ips or []:
            if not isinstance(raw, str) or not raw.strip():
                continue
            try:
                self._directed_ips.append(str(ipaddress.ip_address(raw.strip())))
            except ValueError:
                self._logger.debug("NetBIOS directed_ips skip invalid IP: %r", raw)
        self._nmblookup = shutil.which("nmblookup")
        self._nbtstat: str | None = shutil.which("nbtstat") if sys.platform == "win32" else None
        self._running = False
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._probe_thread: threading.Thread | None = None
        self._missing_logged = False
        self._extra_directed_lock = threading.Lock()
        # IPs to probe with ``nmblookup -A`` (e.g. suggested when WSD only has a synthetic label).
        self._extra_directed: OrderedDict[str, None] = OrderedDict()
        self._max_extra_directed = 64
        # TTL tracking: emit offline for hosts absent ≥ _nmb_grace_sweeps consecutive sweeps.
        self._nmb_known: dict[str, dict] = {}  # ip → last emitted payload
        self._nmb_miss_counts: dict[str, int] = {}  # ip → consecutive missed sweeps
        self._nmb_grace_sweeps: int = 2

    def suggest_directed_ip(self, ip_s: str) -> None:
        """Queue ``nmblookup -A`` / ``nbtstat -A`` for this IP on the next probe."""
        if not self._running or (not self._nmblookup and not self._nbtstat):
            return
        try:
            normalized = str(ipaddress.ip_address((ip_s or "").strip()))
        except ValueError:
            return
        with self._extra_directed_lock:
            self._extra_directed.pop(normalized, None)
            self._extra_directed[normalized] = None
            while len(self._extra_directed) > self._max_extra_directed:
                self._extra_directed.popitem(last=False)
        self._logger.debug("NetBIOS: extra directed probe queued for %s", normalized)
        self.refresh()

    def remove_directed_ip(self, ip_s: str) -> None:
        """Remove an IP from the extra directed probe queue (e.g. device went offline)."""
        try:
            normalized = str(ipaddress.ip_address((ip_s or "").strip()))
        except ValueError:
            return
        with self._extra_directed_lock:
            self._extra_directed.pop(normalized, None)
        self._logger.debug("NetBIOS: removed directed probe for %s", normalized)

    def start(self) -> None:
        if self._running:
            return
        if not self._nmblookup and not self._nbtstat:
            if not self._missing_logged:
                self._logger.warning(
                    "NetBIOS discovery unavailable: nmblookup not in PATH "
                    "(install samba-common-bin or samba-common; ensure nmbd can answer on the LAN)"
                )
                self._missing_logged = True
            return
        self._running = True
        self._stop_event.clear()
        self._wake_event.clear()
        self._probe_thread = threading.Thread(target=self._probe_loop, name="nmb-probe", daemon=True)
        self._probe_thread.start()
        self._logger.info(
            "NetBIOS discovery started (interval=%.1fs timeout=%.1fs argv=%s)",
            self._interval_s,
            self._timeout_s,
            self._argv_rest,
        )

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        self._wake_event.set()
        if self._probe_thread is not None:
            self._probe_thread.join(timeout=2.0)
            self._probe_thread = None
        self._nmb_known.clear()
        self._nmb_miss_counts.clear()
        self._logger.info("NetBIOS discovery stopped")

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
        if not self._nmblookup and not self._nbtstat:
            return
        rows: list[tuple[str, str, str]] = []
        sweep_rc: int | None = None

        # Broadcast sweep: nmblookup only (nbtstat has no broadcast mode).
        if self._nmblookup:
            cmd = [self._nmblookup, *self._argv_rest]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=float(self._timeout_s),
                    check=False,
                    **_NMB_SUBPROCESS_KW,
                )
                sweep_rc = proc.returncode
            except subprocess.TimeoutExpired:
                self._logger.debug("nmblookup timed out after %.1fs", self._timeout_s)
                return
            except OSError:
                self._logger.debug("nmblookup failed to run", exc_info=True)
                return
            out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            rows = _parse_nmblookup_output(out)

        with self._extra_directed_lock:
            extra_directed = list(self._extra_directed.keys())
        directed_chain = list(dict.fromkeys([*self._directed_ips, *sorted(extra_directed)]))

        def _probe_one(dip: str) -> list[tuple[str, str, str]]:
            if self._nmblookup:
                try:
                    proc_d = subprocess.run(
                        [self._nmblookup, "-A", dip],
                        capture_output=True,
                        text=True,
                        timeout=float(self._timeout_s),
                        check=False,
                        **_NMB_SUBPROCESS_KW,
                    )
                    return _parse_nmblookup_output((proc_d.stdout or "") + "\n" + (proc_d.stderr or ""))
                except subprocess.TimeoutExpired:
                    self._logger.debug("nmblookup -A %s timed out after %.1fs", dip, self._timeout_s)
                except OSError:
                    self._logger.debug("nmblookup -A %s failed", dip, exc_info=True)
            elif self._nbtstat:
                try:
                    proc_d = subprocess.run(
                        [self._nbtstat, "-A", dip],
                        capture_output=True,
                        text=True,
                        timeout=float(self._timeout_s),
                        check=False,
                        **_NMB_SUBPROCESS_KW,
                    )
                    result = _parse_nbtstat_output((proc_d.stdout or "") + "\n" + (proc_d.stderr or ""), dip)
                    if result:
                        return result
                except subprocess.TimeoutExpired:
                    self._logger.debug("nbtstat -A %s timed out after %.1fs — trying DNS reverse lookup", dip, self._timeout_s)
                except OSError:
                    self._logger.debug("nbtstat -A %s failed", dip, exc_info=True)
            # Fallback: DNS reverse lookup (covers DNS-registered hosts when UDP 137 is firewalled).
            try:
                import socket as _socket
                fqdn, _, _ = _socket.gethostbyaddr(dip)
                short = fqdn.split(".")[0].strip().upper()
                if short:
                    self._logger.debug("DNS reverse fallback for %s → %s", dip, short)
                    return [(dip, short, "00")]
            except Exception:
                pass
            return []

        if directed_chain:
            workers = min(8, len(directed_chain))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                for partial_rows in pool.map(_probe_one, directed_chain):
                    rows.extend(partial_rows)
        merged = _merge_rows_prefer_ipv4(rows)
        if not merged:
            if sweep_rc is not None:
                self._logger.debug(
                    "NetBIOS: no hosts parsed (nmblookup exit=%s; try ``nmblookup -S '*'`` / LAN NMB)",
                    sweep_rc,
                )
                if self._directed_ips:
                    self._logger.warning(
                        "NetBIOS: directed_ips=%s returned no usable names — check ``nmblookup -A <ip>`` from this host",
                        self._directed_ips,
                    )

        # Build payloads for hosts found in this sweep.
        current_payloads: dict[str, dict] = {}
        for ip_s, name, suffix_primary in merged:
            current_payloads[ip_s] = {
                "name": name,
                "ip": ip_s,
                "port": 445,
                "type": "computer",
                "category": _("Computers"),
                "source": self.source,
                "url": None,
                "metadata": {
                    "nmb_name": name,
                    "nmb_suffix": suffix_primary,
                    "nmb_argv": list(self._argv_rest),
                },
                "online": True,
                "icon": None,
            }

        # TTL: emit offline for previously-known hosts absent from this sweep for ≥ grace sweeps.
        for ip in list(self._nmb_known):
            if ip not in current_payloads:
                miss = self._nmb_miss_counts.get(ip, 0) + 1
                self._nmb_miss_counts[ip] = miss
                if miss >= self._nmb_grace_sweeps:
                    offline_payload = dict(self._nmb_known[ip])
                    offline_payload["online"] = False
                    self._emit("device", offline_payload)
                    self._logger.info(
                        "NetBIOS: ip=%s offline after %d missed sweep(s)", ip, miss
                    )
                    del self._nmb_known[ip]
                    self._nmb_miss_counts.pop(ip, None)
            else:
                self._nmb_miss_counts.pop(ip, None)

        if current_payloads:
            self._logger.info("NetBIOS: publishing %d host row(s)", len(current_payloads))
            self._logger.debug(
                "NetBIOS host rows (ip=name): %s",
                ", ".join(f"{ip}={p['name']}" for ip, p in current_payloads.items()),
            )
        for ip_s, payload in current_payloads.items():
            self._nmb_known[ip_s] = payload
            self._emit("device", payload)
