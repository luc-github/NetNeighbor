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
from pathlib import Path

_LOG = logging.getLogger(__name__)

# Short-lived cache: list refresh / details may call many times in one frame.
_NEIGH_FULL_CACHE: tuple[float, str | None] = (0.0, None)
_ARP_TEXT_CACHE: tuple[float, str | None] = (0.0, None)
_WIN_ARP_CACHE: tuple[float, str | None] = (0.0, None)
_WIN_IPV6_NEIGH_CACHE: tuple[float, str | None] = (0.0, None)

_LLADDR_RE = re.compile(r"\blladdr\s+([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\b")


def _monotonic() -> float:
    try:
        import time

        return time.monotonic()
    except Exception:
        return 0.0


def _read_proc_arp_text_cached() -> str:
    global _ARP_TEXT_CACHE
    now = _monotonic()
    if _ARP_TEXT_CACHE[1] is not None and now - _ARP_TEXT_CACHE[0] < 0.85:
        return _ARP_TEXT_CACHE[1]
    try:
        text = Path("/proc/net/arp").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    _ARP_TEXT_CACHE = (now, text)
    return text


def _ip_neigh_show_all_cached() -> str:
    global _NEIGH_FULL_CACHE
    now = _monotonic()
    if _NEIGH_FULL_CACHE[1] is not None and now - _NEIGH_FULL_CACHE[0] < 0.85:
        return _NEIGH_FULL_CACHE[1]
    out = _run_ip_neigh(["neigh", "show"])
    _NEIGH_FULL_CACHE = (now, out)
    return out


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


def _win_arp_table_cached() -> str:
    """Run ``arp -a`` once and cache the result for 0.85 s (same TTL as Linux caches)."""
    global _WIN_ARP_CACHE
    now = _monotonic()
    if _WIN_ARP_CACHE[1] is not None and now - _WIN_ARP_CACHE[0] < 0.85:
        return _WIN_ARP_CACHE[1]
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
    _WIN_ARP_CACHE = (now, text)
    return text


def _win_ipv6_neigh_cached() -> str:
    global _WIN_IPV6_NEIGH_CACHE
    now = _monotonic()
    if _WIN_IPV6_NEIGH_CACHE[1] is not None and now - _WIN_IPV6_NEIGH_CACHE[0] < 0.85:
        return _WIN_IPV6_NEIGH_CACHE[1]
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
    _WIN_IPV6_NEIGH_CACHE = (now, text)
    return text


def _mac_from_windows_ipv6_neigh(ipv6_raw: str) -> str | None:
    """Resolve MAC for an IPv6 address from ``netsh interface ipv6 show neighbors``."""
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


def lookup_mac_from_neighbor_cache(ip_raw: str | None) -> str | None:
    """Return MAC from OS neighbor/ARP tables if known (Linux ``ip neigh`` / ``/proc/net/arp``, Windows ``arp -a``)."""
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
        out = _run_ip_neigh(["neigh", "show", base])
        hit = _parse_ip_neigh_stdout(out, base)
        if hit:
            return hit
        out = _ip_neigh_show_all_cached()
        return _parse_ip_neigh_stdout(out, base)

    out = _run_ip_neigh(["neigh", "show", base])
    hit = _parse_ip_neigh_stdout(out, raw)
    if hit:
        return hit
    hit = _parse_ip_neigh_stdout(out, base)
    if hit:
        return hit
    out = _ip_neigh_show_all_cached()
    hit = _parse_ip_neigh_stdout(out, raw)
    if hit:
        return hit
    return _parse_ip_neigh_stdout(out, base)


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
    """Pick a LAN IPv4 for this MAC from kernel ARP / neighbor tables (Linux).

    Complements :func:`lookup_mac_from_neighbor_cache`: when discovery only shows IPv6 but the
    neighbor cache already has an IPv4 for the same ``lladdr``, return it (still no extra packets).
    """
    m = _norm_mac(mac_raw or "")
    if not m:
        return None
    s = _ipv4_addrs_for_mac_from_proc_arp(m) | _ipv4_addrs_for_mac_from_neigh_show(m)
    if not s:
        return None
    return _pick_preferred_ipv4(list(s))
