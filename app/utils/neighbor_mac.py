# File neighbor_mac.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Resolve MAC from kernel neighbor caches (ARP / IPv6 ND) — no privileged probe.

Reads ``/proc/net/arp`` and ``ip neigh show``. Entries appear after recent LAN traffic;
this does not send new packets (no ping / gratuitous ARP).
"""

from __future__ import annotations

import ipaddress
import logging
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

_LOG = logging.getLogger(__name__)

# Neighbour tables are read for every device on every list rebuild, from the UI thread and
# from several worker threads (ping, NetBIOS, discovery). Each table is therefore cached
# and refreshed *single-flight*: one refresh at a time, concurrent callers wait for it and
# reuse its result. Without the lock, every caller that saw an expired cache spawned its
# own ``arp -a`` — on Windows that piled up hundreds of hidden arp.exe/conhost.exe and
# exhausted the desktop heap.
_TABLE_TTL_S = 2.0


class _SingleFlightCache:
    def __init__(self, loader: Callable[[], object], ttl_s: float = _TABLE_TTL_S) -> None:
        self._loader = loader
        self._ttl_s = ttl_s
        self._lock = threading.Lock()
        self._stamp = 0.0
        self._value: object = None

    def get(self):
        with self._lock:
            now = _monotonic()
            if self._value is not None and now - self._stamp < self._ttl_s:
                return self._value
            try:
                value = self._loader()
            except Exception:  # never let a failed refresh re-trigger on every call
                _LOG.debug("neighbour table refresh failed", exc_info=True)
                value = None
            # Cache failures too (as an empty table) so a broken tool is not re-run per call.
            self._value = value if value is not None else ""
            self._stamp = _monotonic()
            return self._value

# macOS ``arp -an`` line: ``? (192.168.1.1) at aa:bb:cc:dd:ee:ff on en0 ...``
_DARWIN_ARP_RE = re.compile(
    r"\(([^)]+)\)\s+at\s+([0-9a-fA-F]{1,2}(?::[0-9a-fA-F]{1,2}){5})\b"
)

_LLADDR_RE = re.compile(r"\blladdr\s+([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\b")


def _monotonic() -> float:
    try:
        import time

        return time.monotonic()
    except Exception:
        return 0.0


def _load_proc_arp_text() -> str:
    try:
        return Path("/proc/net/arp").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


_PROC_ARP_CACHE = _SingleFlightCache(_load_proc_arp_text)


def _read_proc_arp_text_cached() -> str:
    return _PROC_ARP_CACHE.get()


_NEIGH_FULL_CACHE = _SingleFlightCache(lambda: _run_ip_neigh(["neigh", "show"]))


def _ip_neigh_show_all_cached() -> str:
    return _NEIGH_FULL_CACHE.get()


def _norm_mac(s: str) -> str | None:
    t = (s or "").strip().lower()
    if t.count("-") == 5 and ":" not in t:
        t = t.replace("-", ":")
    if len(t) == 17 and t.count(":") == 5:
        parts = t.split(":")
        if all(len(p) == 2 and all(c in "0123456789abcdef" for c in p) for p in parts):
            return ":".join(parts)
    return None


def _ip_matches(neighbor_col: str, want_raw: str) -> bool:
    na = neighbor_col.split("%", 1)[0].strip()
    wb = want_raw.split("%", 1)[0].strip()
    try:
        return ipaddress.ip_address(na) == ipaddress.ip_address(wb)
    except ValueError:
        return na == wb


def _mac_from_proc_arp(ipv4: str) -> str | None:
    text = _read_proc_arp_text_cached()
    if not text:
        return None
    target = ipv4.strip()
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        if parts[0] != target:
            continue
        hw = parts[3]
        if hw == "00:00:00:00:00:00":
            continue
        m = _norm_mac(hw)
        if m:
            return m
    return None


def _parse_ip_neigh_stdout(stdout: str, want_ip: str) -> str | None:
    want_ip = want_ip.strip()
    if not want_ip:
        return None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if not parts:
            continue
        if not _ip_matches(parts[0], want_ip):
            continue
        mo = _LLADDR_RE.search(line)
        if mo:
            got = _norm_mac(mo.group(1))
            if got:
                return got
    return None


def _run_ip_neigh(args: list[str]) -> str:
    if sys.platform == "win32" or sys.platform == "darwin":
        return ""
    try:
        proc = subprocess.run(
            ["ip", *args],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        _LOG.debug("ip neigh failed: %s", e)
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


def _load_win_arp_text() -> str:
    """Fallback only: ``arp -a`` (used when the native neighbour API is unavailable)."""
    _cflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            ["arp", "-a"],
            capture_output=True,
            text=True,
            timeout=4,
            encoding="utf-8",
            errors="replace",
            creationflags=_cflags,
        )
        text = proc.stdout or "" if proc.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        _LOG.debug("arp -a failed: %s", e)
        text = ""
    return text


_WIN_ARP_TEXT_CACHE = _SingleFlightCache(_load_win_arp_text)


def _win_arp_table_cached() -> str:
    return _WIN_ARP_TEXT_CACHE.get()


def _load_win_ipv6_neigh_text() -> str:
    """Fallback only: ``netsh interface ipv6 show neighbors``."""
    _cflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            ["netsh", "interface", "ipv6", "show", "neighbors"],
            capture_output=True,
            text=True,
            timeout=5,
            encoding="utf-8",
            errors="replace",
            creationflags=_cflags,
        )
        text = proc.stdout or "" if proc.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        _LOG.debug("netsh ipv6 neigh failed: %s", e)
        text = ""
    return text


_WIN_IPV6_TEXT_CACHE = _SingleFlightCache(_load_win_ipv6_neigh_text)


def _win_ipv6_neigh_cached() -> str:
    return _WIN_IPV6_TEXT_CACHE.get()


def _load_win_native_neighbors() -> dict[str, str] | None:
    """Read the IPv4+IPv6 neighbour table via ``GetIpNetTable2`` — no child process.

    Returns ``{ip_text: mac}`` (IPv6 without zone), or None when the API is unavailable
    so callers fall back to ``arp -a`` / ``netsh``.
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _SockaddrInet(ctypes.Structure):  # SOCKADDR_INET (28 bytes)
        _fields_ = [("family", ctypes.c_ushort), ("data", ctypes.c_ubyte * 26)]

    class _MibIpnetRow2(ctypes.Structure):  # MIB_IPNET_ROW2 (88 bytes)
        _fields_ = [
            ("Address", _SockaddrInet),
            ("InterfaceIndex", wintypes.ULONG),
            ("InterfaceLuid", ctypes.c_ulonglong),
            ("PhysicalAddress", ctypes.c_ubyte * 32),
            ("PhysicalAddressLength", wintypes.ULONG),
            ("State", ctypes.c_int),
            ("Flags", ctypes.c_ubyte),
            ("ReachabilityTime", wintypes.ULONG),
        ]

    if ctypes.sizeof(_MibIpnetRow2) != 88:
        return None
    try:
        iphlpapi = ctypes.WinDLL("iphlpapi.dll")
        get_table = iphlpapi.GetIpNetTable2
        free_table = iphlpapi.FreeMibTable
    except (OSError, AttributeError):
        return None
    get_table.argtypes = [ctypes.c_ushort, ctypes.POINTER(ctypes.c_void_p)]
    get_table.restype = wintypes.ULONG
    free_table.argtypes = [ctypes.c_void_p]
    free_table.restype = None

    af_inet, af_inet6 = 2, 23
    nlns_incomplete = 1  # states 0 (unreachable) and 1 (incomplete) carry no usable MAC
    table = ctypes.c_void_p()
    if get_table(0, ctypes.byref(table)) != 0 or not table.value:  # AF_UNSPEC
        return None
    out: dict[str, str] = {}
    try:
        count = wintypes.ULONG.from_address(table.value).value
        # MIB_IPNET_TABLE2: ULONG NumEntries, then rows aligned on 8 bytes.
        rows = (_MibIpnetRow2 * count).from_address(table.value + 8)
        for row in rows:
            if row.State <= nlns_incomplete or row.PhysicalAddressLength != 6:
                continue
            hw = bytes(row.PhysicalAddress[:6])
            if hw in (b"\x00" * 6, b"\xff" * 6):
                continue
            data = bytes(row.Address.data)
            if row.Address.family == af_inet:
                ip_txt = str(ipaddress.IPv4Address(data[2:6]))
            elif row.Address.family == af_inet6:
                ip_txt = str(ipaddress.IPv6Address(data[6:22]))
            else:
                continue
            out.setdefault(ip_txt, ":".join(f"{b:02x}" for b in hw))
    finally:
        free_table(table)
    return out


_WIN_NATIVE_UNAVAILABLE = object()
def _load_win_native_or_marker() -> object:
    got = _load_win_native_neighbors()
    return _WIN_NATIVE_UNAVAILABLE if got is None else got  # an empty table is still valid


_WIN_NATIVE_CACHE = _SingleFlightCache(_load_win_native_or_marker)


def _win_native_neighbors() -> dict[str, str] | None:
    got = _WIN_NATIVE_CACHE.get()
    return got if isinstance(got, dict) else None


def _mac_from_windows_ipv6_neigh(ipv6_raw: str) -> str | None:
    """Resolve MAC for an IPv6 address (native table, else ``netsh``)."""
    native = _win_native_neighbors()
    if native is not None:
        try:
            return native.get(str(ipaddress.ip_address(ipv6_raw.split("%", 1)[0].strip())))
        except ValueError:
            return None
    text = _win_ipv6_neigh_cached()
    if not text:
        return None
    want_base = ipv6_raw.split("%", 1)[0].strip()
    try:
        want_addr = ipaddress.ip_address(want_base)
    except ValueError:
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        col_ip = parts[0].split("%", 1)[0].strip()
        try:
            col_addr = ipaddress.ip_address(col_ip)
        except ValueError:
            continue
        if col_addr != want_addr:
            continue
        for col in parts[1:]:
            m = _norm_mac(col)
            if m and m != "00:00:00:00:00:00":
                return m
    return None


def _mac_from_windows_arp(ipv4: str) -> str | None:
    """Read ``arp -a`` on Windows (no extra packets; entry appears after LAN traffic)."""
    if sys.platform != "win32":
        return None
    target = ipv4.strip()
    if not target:
        return None
    native = _win_native_neighbors()
    if native is not None:
        return native.get(target)
    for line in _win_arp_table_cached().splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[0] != target:
            continue
        m = _norm_mac(parts[1])
        if m and m != "00:00:00:00:00:00":
            return m
    return None


def _load_darwin_arp_text() -> str:
    try:
        proc = subprocess.run(
            ["arp", "-an"],
            capture_output=True, text=True, timeout=4,
            encoding="utf-8", errors="replace",
        )
        text = proc.stdout or "" if proc.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        _LOG.debug("arp -an failed: %s", e)
        text = ""
    return text


_DARWIN_ARP_CACHE = _SingleFlightCache(_load_darwin_arp_text)


def _darwin_arp_table_cached() -> str:
    return _DARWIN_ARP_CACHE.get()


def _load_darwin_ndp_text() -> str:
    try:
        proc = subprocess.run(
            ["ndp", "-an"],
            capture_output=True, text=True, timeout=4,
            encoding="utf-8", errors="replace",
        )
        text = proc.stdout or "" if proc.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        _LOG.debug("ndp -an failed: %s", e)
        text = ""
    return text


_DARWIN_NDP_CACHE = _SingleFlightCache(_load_darwin_ndp_text)


def _darwin_ndp_table_cached() -> str:
    return _DARWIN_NDP_CACHE.get()


def _mac_from_darwin_arp(ipv4: str) -> str | None:
    target = ipv4.strip()
    for line in _darwin_arp_table_cached().splitlines():
        m = _DARWIN_ARP_RE.search(line)
        if not m:
            continue
        try:
            if ipaddress.ip_address(m.group(1).strip()) != ipaddress.ip_address(target):
                continue
        except ValueError:
            continue
        mac = _norm_mac(m.group(2))
        if mac and mac != "00:00:00:00:00:00":
            return mac
    return None


def _mac_from_darwin_ndp(ipv6_raw: str) -> str | None:
    want_base = ipv6_raw.split("%", 1)[0].strip()
    try:
        want_addr = ipaddress.ip_address(want_base)
    except ValueError:
        return None
    for line in _darwin_ndp_table_cached().splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        col_ip = parts[0].split("%", 1)[0].strip()
        try:
            col_addr = ipaddress.ip_address(col_ip)
        except ValueError:
            continue
        if col_addr != want_addr:
            continue
        mac = _norm_mac(parts[1])
        if mac and mac != "00:00:00:00:00:00":
            return mac
    return None


def _ipv4_addrs_for_mac_from_darwin_arp(mac_norm: str) -> set[str]:
    out: set[str] = set()
    for line in _darwin_arp_table_cached().splitlines():
        m = _DARWIN_ARP_RE.search(line)
        if not m or _norm_mac(m.group(2)) != mac_norm:
            continue
        ip_tok = m.group(1).strip()
        try:
            a = ipaddress.ip_address(ip_tok)
        except ValueError:
            continue
        if isinstance(a, ipaddress.IPv4Address):
            out.add(str(a))
    return out


def lookup_mac_from_neighbor_cache(ip_raw: str | None) -> str | None:
    """Return MAC from OS neighbor/ARP tables if known (Linux ``ip neigh`` / ``/proc/net/arp``, Windows ``arp -a``, macOS ``arp``/``ndp``)."""
    if not ip_raw:
        return None
    if sys.platform == "win32":
        try:
            base = str(ip_raw).strip().split("%", 1)[0].strip()
            if not base:
                return None
            parsed = ipaddress.ip_address(base)
            if isinstance(parsed, ipaddress.IPv4Address):
                return _mac_from_windows_arp(base)
            return _mac_from_windows_ipv6_neigh(str(ip_raw).strip())
        except ValueError:
            pass
        return None
    if sys.platform == "darwin":
        try:
            base = str(ip_raw).strip().split("%", 1)[0].strip()
            if not base:
                return None
            parsed = ipaddress.ip_address(base)
            if isinstance(parsed, ipaddress.IPv4Address):
                return _mac_from_darwin_arp(base)
            return _mac_from_darwin_ndp(str(ip_raw).strip())
        except ValueError:
            pass
        return None
    raw = str(ip_raw).strip()
    base = raw.split("%", 1)[0].strip()
    if not base:
        return None
    try:
        parsed = ipaddress.ip_address(base)
    except ValueError:
        return None

    if isinstance(parsed, ipaddress.IPv4Address):
        hit = _mac_from_proc_arp(base)
        if hit:
            return hit
        # Shared cached table only — no per-IP ``ip neigh show <ip>`` spawn per lookup.
        out = _ip_neigh_show_all_cached()
        return _parse_ip_neigh_stdout(out, base)

    out = _ip_neigh_show_all_cached()
    hit = _parse_ip_neigh_stdout(out, raw)
    if hit:
        return hit
    return _parse_ip_neigh_stdout(out, base)


def _ipv4_addrs_for_mac_from_win_arp(mac_norm: str) -> set[str]:
    """Find IPv4 addresses for a MAC in the Windows neighbour table."""
    out: set[str] = set()
    native = _win_native_neighbors()
    if native is not None:
        for ip_txt, mac in native.items():
            if mac == mac_norm and ":" not in ip_txt:
                out.add(ip_txt)
        return out
    for line in _win_arp_table_cached().splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        m = _norm_mac(parts[1])
        if m != mac_norm:
            continue
        ip_tok = parts[0].strip()
        try:
            a = ipaddress.ip_address(ip_tok)
        except ValueError:
            continue
        if isinstance(a, ipaddress.IPv4Address):
            out.add(str(a))
    return out


def _ipv4_addrs_for_mac_from_proc_arp(mac_norm: str) -> set[str]:
    out: set[str] = set()
    text = _read_proc_arp_text_cached()
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        hw = _norm_mac(parts[3])
        if hw != mac_norm:
            continue
        ip_tok = parts[0].strip()
        try:
            a = ipaddress.ip_address(ip_tok)
        except ValueError:
            continue
        if isinstance(a, ipaddress.IPv4Address):
            out.add(str(a))
    return out


def _ipv4_addrs_for_mac_from_neigh_show(mac_norm: str) -> set[str]:
    out: set[str] = set()
    stdout = _ip_neigh_show_all_cached()
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        mo = _LLADDR_RE.search(line)
        if not mo or _norm_mac(mo.group(1)) != mac_norm:
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            a = ipaddress.ip_address(parts[0].split("%", 1)[0])
        except ValueError:
            continue
        if isinstance(a, ipaddress.IPv4Address):
            out.add(str(a))
    return out


def _pick_preferred_ipv4(candidates: list[str]) -> str | None:
    v4s: list[ipaddress.IPv4Address] = []
    for c in candidates:
        try:
            a = ipaddress.ip_address(c.strip())
            if isinstance(a, ipaddress.IPv4Address):
                v4s.append(a)
        except ValueError:
            continue
    if not v4s:
        return None

    def sort_key(a: ipaddress.IPv4Address) -> tuple[int, int]:
        if a.is_private:
            return (0, int(a))
        if a.is_global:
            return (1, int(a))
        return (2, int(a))

    v4s.sort(key=sort_key)
    return str(v4s[0])


def lookup_ipv4_for_mac(mac_raw: str | None) -> str | None:
    """Pick a LAN IPv4 for this MAC from kernel ARP / neighbor tables.

    Complements :func:`lookup_mac_from_neighbor_cache`: when discovery only shows IPv6 but the
    neighbor cache already has an IPv4 for the same ``lladdr``, return it (still no extra packets).
    """
    m = _norm_mac(mac_raw or "")
    if not m:
        return None
    if sys.platform == "win32":
        s = _ipv4_addrs_for_mac_from_win_arp(m)
    elif sys.platform == "darwin":
        s = _ipv4_addrs_for_mac_from_darwin_arp(m)
    else:
        s = _ipv4_addrs_for_mac_from_proc_arp(m) | _ipv4_addrs_for_mac_from_neigh_show(m)
    if not s:
        return None
    return _pick_preferred_ipv4(list(s))
