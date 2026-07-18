# File local_host.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-07-18 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Local machine addresses: enumerate them and rank candidate IPs for multi-homed hosts.

The machine running NetNeighbor is a special discovery case (see docs/reference/MDNS.md,
"Multi-homed hosts and the local machine"):

- A Windows host commonly holds several IPv4 addresses at once — the real LAN NIC (DHCP),
  VirtualBox / Hyper-V host-only switches (192.168.56.1, ...), and APIPA 169.254.x
  fallbacks on disconnected adapters. mDNS advertises ALL of them in one service record,
  and zeroconf's address ordering is not stable between resolutions.
- Windows never answers WSD probes sent from the machine itself (verified empirically:
  fdrespub running, Private profile — remote hosts respond, the local stack does not),
  and NetBIOS browsing does not return the local host either. So the only rows the app
  ever sees for its own machine are incidental mDNS adverts (e.g. Spotify's
  _spotify-connect._tcp) that carry no "computer" identity.

Hence the two helpers here: pick one stable routable address out of a multi-address
record, and recognise "this IP belongs to the machine NetNeighbor runs on".
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time

# Host-only / hypervisor switch subnets: real neighbours never live there, and the
# addresses only exist on the machines hosting the hypervisor. Shared with SSDP
# multicast interface selection (ssdp.py).
VIRTUAL_HOST_SUBNETS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.ip_network("192.168.56.0/24"),  # VirtualBox host-only
    ipaddress.ip_network("192.168.53.0/24"),  # common Hyper-V / third-party virtual NIC
    ipaddress.ip_network("192.168.122.0/24"),  # libvirt virbr0
    ipaddress.ip_network("192.168.137.0/24"),  # Windows ICS / hotspot host
)

_LOCAL_IP_CACHE_TTL_S = 60.0
_local_ip_cache: tuple[float, frozenset[str]] | None = None
_local_ip_lock = threading.Lock()


def address_preference_rank(ip_s: str) -> int:
    """Lower is better. Routable LAN IPv4 first, virtual-switch IPv4 next, then routable
    IPv6, then link-local (APIPA / fe80), loopback and garbage last."""
    try:
        addr = ipaddress.ip_address(str(ip_s).strip().strip("[]"))
    except ValueError:
        return 9
    if addr.is_loopback or addr.is_multicast or addr.is_unspecified:
        return 8
    if isinstance(addr, ipaddress.IPv4Address):
        if addr.is_link_local:
            return 4
        if any(addr in net for net in VIRTUAL_HOST_SUBNETS):
            return 1
        return 0
    if addr.is_link_local:
        return 5
    return 2


def choose_best_address(addresses: list[str]) -> str:
    """Deterministic best address from a (possibly reordered) mDNS address list.

    zeroconf returns a host's A/AAAA records in unstable order; taking the first entry
    makes the same host flap between its LAN, virtual-switch and APIPA addresses across
    resolutions — each flap materialising as a distinct device row. Ranking + a full
    sort keeps the choice identical for any permutation of the same address set.
    """
    candidates: list[tuple[int, bytes, str]] = []
    for value in addresses:
        text = str(value).strip()
        if not text:
            continue
        rank = address_preference_rank(text)
        if rank >= 8:
            continue
        try:
            sort_key = ipaddress.ip_address(text.strip("[]")).packed
        except ValueError:
            sort_key = text.encode()
        candidates.append((rank, sort_key, text))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def local_ip_addresses() -> frozenset[str]:
    """This machine's non-loopback IP addresses (cached; DHCP renewals refresh within a minute)."""
    global _local_ip_cache
    now = time.monotonic()
    with _local_ip_lock:
        if _local_ip_cache is not None and now - _local_ip_cache[0] <= _LOCAL_IP_CACHE_TTL_S:
            return _local_ip_cache[1]
    found: set[str] = set()
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("203.0.113.1", 9))
            ip = probe.getsockname()[0]
            if ip:
                found.add(ip)
        finally:
            probe.close()
    except OSError:
        pass
    try:
        for res in socket.getaddrinfo(socket.gethostname(), None, proto=socket.IPPROTO_TCP):
            ip = str(res[4][0]).split("%", 1)[0]
            if ip:
                found.add(ip)
    except OSError:
        pass
    normalized: set[str] = set()
    for ip in found:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.is_loopback or addr.is_unspecified:
            continue
        normalized.add(addr.compressed)
    result = frozenset(normalized)
    with _local_ip_lock:
        _local_ip_cache = (now, result)
    return result


def is_local_host_ip(ip_s: str) -> bool:
    """True when this IP belongs to the machine NetNeighbor is running on."""
    text = str(ip_s).strip().strip("[]")
    if not text:
        return False
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    return addr.compressed in local_ip_addresses()
