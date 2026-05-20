# File double_click_open.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Resolve LAN « Open » targets for double-click / Open menu (HTTP(S) from URLs and mDNS, then SMB → …)."""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

from model.device import Device

_LOG = logging.getLogger(__name__)

# DNS-SD / mDNS service type substrings (after normalization, lowercase).
_HTTPS = ("_https._tcp",)
_HTTP = ("_http._tcp",)
_SMB = ("_smb._tcp", "_microsoft-ds._tcp")
_FTP = ("_ftp._tcp",)
_SSH = ("_ssh._tcp",)
_TELNET = ("_telnet._tcp",)

# Double-click priority order
_DCLICK_SCHEME_ORDER = ("http", "smb", "ssh", "ftp", "sftp", "telnet")

_SCHEME_LABELS: dict[str, str] = {
    "https": "HTTPS",
    "http": "HTTP",
    "smb": "SMB",
    "ftp": "FTP",
    "ssh": "SSH",
    "sftp": "SFTP",
    "telnet": "Telnet",
}

_SCHEME_DEFAULT_PORTS: dict[str, int] = {
    "http": 80, "https": 443, "smb": 445, "ftp": 21,
    "ssh": 22, "sftp": 22, "telnet": 23,
}


def _split_zone(bundle_ip: str) -> tuple[str, str | None]:
    ip = (bundle_ip or "").strip()
    if "%" not in ip:
        return ip, None
    base, zone = ip.split("%", 1)
    return base.strip() or ip, zone.strip() or None


def format_host_for_uri(bundle_ip: str, _devices: list[Device]) -> str:
    """Format IP or hostname for smb://, ftp://, ssh://, etc."""
    ip, _zone = _split_zone(bundle_ip)
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        return f"[{addr.compressed}]"
    return addr.exploded


def _first_url_with_scheme(devices: list[Device], scheme: str) -> str | None:
    low = scheme.lower()
    for dev in devices:
        raw = getattr(dev, "url", None)
        if not isinstance(raw, str) or not raw.strip():
            continue
        u = raw.strip()
        if urlparse(u).scheme.lower() == low:
            return u
    return None


def _collect_https_http_urls(devices: list[Device]) -> tuple[list[str], list[str]]:
    https_u: list[str] = []
    http_u: list[str] = []
    for dev in devices:
        # WSD/wsdd URLs are protocol endpoints (port 5357), not web interfaces.
        if (getattr(dev, "source", "") or "").strip().lower() in {"wsd", "wsdd"}:
            continue
        raw = getattr(dev, "url", None)
        if not isinstance(raw, str) or not raw.strip():
            continue
        u = raw.strip()
        scheme = urlparse(u).scheme.lower()
        if scheme == "https":
            https_u.append(u)
        elif scheme == "http":
            http_u.append(u)
    return https_u, http_u


def _url_override_from_devices(devices: list[Device]) -> str | None:
    for dev in devices:
        md = getattr(dev, "metadata", None)
        if isinstance(md, dict):
            v = md.get("url_override")
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _device_commands_from_devices(devices: list[Device]) -> list[dict]:
    """Return per-device command list from metadata (first device that has one)."""
    for dev in devices:
        md = getattr(dev, "metadata", None)
        if isinstance(md, dict):
            cmds = md.get("device_commands")
            if isinstance(cmds, list) and cmds:
                return cmds
    return []


def _build_uri_from_command(scheme: str, cmd: dict, fallback_ip: str) -> str | None:
    """Build a URI for *scheme* from a device_commands entry."""
    ep_ip = str(cmd.get("ip", "")).strip()
    ep_port = int(cmd.get("port", 0) or 0)
    eff_ip = ep_ip if ep_ip else fallback_ip
    if not eff_ip:
        return None
    default_port = _SCHEME_DEFAULT_PORTS.get(scheme, 0)
    if ":" in eff_ip and not eff_ip.startswith("["):
        eff_ip = f"[{eff_ip}]"
    if scheme in ("http", "https", "smb", "ftp"):
        if ep_port and ep_port != default_port:
            return f"{scheme}://{eff_ip}:{ep_port}/"
        return f"{scheme}://{eff_ip}/"
    elif scheme in ("ssh", "sftp"):
        if ep_port and ep_port != default_port:
            return f"{scheme}://{eff_ip}:{ep_port}"
        return f"{scheme}://{eff_ip}"
    elif scheme == "telnet":
        if ep_port and ep_port != default_port:
            return f"telnet://{eff_ip}:{ep_port}"
        return f"telnet://{eff_ip}"
    if ep_port:
        return f"{scheme}://{eff_ip}:{ep_port}"
    return f"{scheme}://{eff_ip}"


def _merged_mdns_services(devices: list[Device]) -> list[dict]:
    seen_keys: set[str] = set()
    result: list[dict] = []
    for dev in devices:
        if getattr(dev, "source", None) != "mdns":
            continue
        md = getattr(dev, "metadata", None)
        if not isinstance(md, dict):
            continue
        svcs = md.get("services")
        if not isinstance(svcs, list):
            continue
        for s in svcs:
            if not isinstance(s, dict):
                continue
            key = str(s.get("service", "")).lower()
            if key and key not in seen_keys:
                seen_keys.add(key)
                result.append(s)
    return result


def _service_match(services: list[dict], needles: tuple[str, ...]) -> dict | None:
    for svc in services:
        name = str(svc.get("service", "")).lower()
        if any(n in name for n in needles):
            return svc
    return None


def _port(svc: dict | None, default: int) -> int:
    if svc is None:
        return default
    p = int(svc.get("port", 0) or 0)
    return p if p > 0 else default


def _mdns_http_url(svc: dict | None, *, https: bool, host: str) -> str | None:
    if svc is None:
        return None
    default = 443 if https else 80
    p = _port(svc, default)
    scheme = "https" if https else "http"
    if p == default:
        return f"{scheme}://{host}/"
    return f"{scheme}://{host}:{p}/"


_MDNS_NEEDLES = {
    "http": _HTTP, "https": _HTTPS,
    "smb": _SMB, "ftp": _FTP, "ssh": _SSH, "telnet": _TELNET,
}


def _detected_uri_for_scheme(
    scheme: str,
    devices: list[Device],
    services: list[dict],
    host: str,
) -> str | None:
    """Return the auto-discovered URI for *scheme*, or None."""
    if scheme in ("http", "https"):
        https_u, http_u = _collect_https_http_urls(devices)
        if scheme == "https" and https_u:
            return https_u[0]
        if scheme == "http" and http_u:
            return http_u[0]
        needles = _MDNS_NEEDLES.get(scheme)
        if needles:
            svc = _service_match(services, needles)
            return _mdns_http_url(svc, https=(scheme == "https"), host=host)
        return None

    found = _first_url_with_scheme(devices, scheme)
    if found:
        return found

    needles = _MDNS_NEEDLES.get(scheme)
    if needles:
        svc = _service_match(services, needles)
        if svc is not None:
            dp = _SCHEME_DEFAULT_PORTS.get(scheme, 0)
            p = _port(svc, dp)
            if scheme in ("smb", "ftp"):
                return f"{scheme}://{host}/" if p == dp else f"{scheme}://{host}:{p}/"
            else:
                return f"{scheme}://{host}" if p == dp else f"{scheme}://{host}:{p}"
    return None


def _command_label(scheme: str, cmd: dict) -> str:
    """Human-readable label for a device_commands entry."""
    base = _SCHEME_LABELS.get(scheme, scheme.upper())
    lbl = str(cmd.get("label", "")).strip()
    mode = str(cmd.get("mode", "override")).strip().lower()
    if lbl:
        return f"{base} ({lbl})"
    if mode == "override":
        return f"{base} (override)"
    return base


def resolve_connect_target(
    *,
    bundle_ip: str,
    primary_type: str,
    devices: list[Device],
) -> str | None:
    """
    Return the best URI for double-click, following the priority:
    http → smb → ssh → ftp → sftp → telnet
    For each scheme: override > detected default > first additional.
    Falls back to legacy url_override, then computer→smb heuristic.
    """
    if not devices:
        return None

    # Legacy url_override takes absolute priority (backward compat)
    url_override = _url_override_from_devices(devices)
    if url_override:
        return url_override

    host = format_host_for_uri(bundle_ip, devices)
    services = _merged_mdns_services(devices)
    device_cmds = _device_commands_from_devices(devices)

    for scheme in _DCLICK_SCHEME_ORDER:
        # 1. Override for this scheme
        override = next(
            (c for c in device_cmds if c.get("scheme") == scheme and c.get("mode") == "override"),
            None,
        )
        if override is not None:
            u = _build_uri_from_command(scheme, override, host)
            if u:
                return u

        # 2. Auto-detected default
        detected = _detected_uri_for_scheme(scheme, devices, services, host)
        if detected:
            _LOG.debug("resolve_connect: detected scheme=%s uri=%s", scheme, detected)
            return detected

        # 3. First additional for this scheme
        additional = next(
            (c for c in device_cmds if c.get("scheme") == scheme and c.get("mode") == "additional"),
            None,
        )
        if additional is not None:
            u = _build_uri_from_command(scheme, additional, host)
            if u:
                return u

    _LOG.debug("resolve_connect: no target found for ip=%s type=%s", bundle_ip, primary_type)
    return None


def resolve_all_connect_targets(
    *,
    bundle_ip: str,
    primary_type: str,
    devices: list[Device],
) -> list[tuple[str, str]]:
    """Return ALL available connection targets as (label, uri) pairs, in priority order.

    Label format:
      - Auto-detected:  "HTTP", "SMB", …
      - Override entry: "HTTP (override)" or "HTTP (label)" if labelled
      - Additional:     "SSH (Admin)", "HTTP (Caméra 2)", …
    """
    if not devices:
        return []

    result: list[tuple[str, str]] = []
    seen_uris: set[str] = set()
    host = format_host_for_uri(bundle_ip, devices)
    services = _merged_mdns_services(devices)
    device_cmds = _device_commands_from_devices(devices)

    def _add(label: str, uri: str) -> None:
        if uri and uri not in seen_uris:
            seen_uris.add(uri)
            result.append((label, uri))

    # Legacy url_override (backward compat, shown as HTTP override)
    url_override = _url_override_from_devices(devices)
    if url_override:
        from urllib.parse import urlparse as _up
        s = _up(url_override).scheme.lower()
        _add(f"{_SCHEME_LABELS.get(s, s.upper())} (override)", url_override)

    for scheme in _DCLICK_SCHEME_ORDER:
        base = _SCHEME_LABELS.get(scheme, scheme.upper())

        # Override entry
        override = next(
            (c for c in device_cmds if c.get("scheme") == scheme and c.get("mode") == "override"),
            None,
        )
        if override is not None:
            u = _build_uri_from_command(scheme, override, host)
            if u:
                _add(_command_label(scheme, override), u)

        # Auto-detected
        detected = _detected_uri_for_scheme(scheme, devices, services, host)
        if detected:
            _add(base, detected)

        # All additionals for this scheme
        for cmd in device_cmds:
            if cmd.get("scheme") == scheme and cmd.get("mode") == "additional":
                u = _build_uri_from_command(scheme, cmd, host)
                if u:
                    _add(_command_label(scheme, cmd), u)

    return result
