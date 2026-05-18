# File device_remote_icon.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Device-provided icon URLs and local cache index (shared GTK/Qt, no UI deps)."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from urllib.request import urlopen

from model.device import Device
from utils.device_bundles import DeviceBundle


def _netneighbor_cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "netneighbor" / "cache"
    return Path.home() / ".cache" / "netneighbor"


_CACHE_BASE = _netneighbor_cache_dir()
_REMOTE_ICON_DISK_DIR = _CACHE_BASE / "remote_icons"
_REMOTE_ICON_INDEX_PATH = _CACHE_BASE / "remote_icon_index.json"
_index_cache: dict[str, dict[str, str]] | None = None


def _load_index() -> dict[str, dict[str, str]]:
    global _index_cache
    if _index_cache is not None:
        return _index_cache
    out: dict[str, dict[str, str]] = {}
    try:
        if _REMOTE_ICON_INDEX_PATH.is_file():
            data = json.loads(_REMOTE_ICON_INDEX_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and int(data.get("version", 0)) >= 2:
                hosts = data.get("hosts")
                if isinstance(hosts, dict):
                    for sip, row in hosts.items():
                        if isinstance(sip, str) and isinstance(row, dict):
                            canon = row.get("canonical_url")
                            sha = row.get("payload_sha256")
                            if (
                                isinstance(canon, str)
                                and canon.strip()
                                and isinstance(sha, str)
                                and sha.strip()
                            ):
                                out[sip.strip()] = {
                                    "canonical_url": canon.strip(),
                                    "payload_sha256": sha.strip(),
                                }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    _index_cache = out
    return out


def cached_remote_icon_url_for_host(ip: str) -> str | None:
    sip = str(ip).strip()
    if not sip or sip in {"0.0.0.0", "::"}:
        return None
    row = _load_index().get(sip)
    if not row:
        return None
    payload_path = _REMOTE_ICON_DISK_DIR / row["payload_sha256"]
    if not payload_path.is_file():
        return None
    return row["canonical_url"]


def _normalize_icon_url(ref: str, ip: str, port: int) -> str:
    ref = ref.strip()
    if ref.startswith(("http://", "https://")):
        return rewrite_remote_icon_url_to_device_ip(ref, ip)
    try:
        p = int(port) if port else 80
    except (TypeError, ValueError):
        p = 80
    sip = str(ip).strip()
    if not sip or sip in {"0.0.0.0", "::"}:
        sip = "127.0.0.1"
    base = f"http://{sip}:{p}"
    if ref.startswith("/"):
        return base + ref
    return f"{base}/{ref.lstrip('/')}"


_MDNS_ICON_TXT_KEYS = frozenset(
    {"representation", "icon", "iconurl", "apple-touch-icon", "favicon"}
)
_MDNS_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")


def _mdns_preferred_icon_base_port(metadata: dict, fallback_port: int) -> int:
    """Use ``_http._tcp`` port when present (Sonos and similar serve icons there)."""
    if not isinstance(metadata, dict):
        return fallback_port
    services = metadata.get("services")
    if not isinstance(services, list):
        return fallback_port
    for svc in services:
        if not isinstance(svc, dict):
            continue
        name = str(svc.get("service", "")).lower()
        if "_http._tcp" not in name:
            continue
        try:
            candidate = int(svc.get("port", 0) or 0)
        except (TypeError, ValueError):
            continue
        if candidate > 0:
            return candidate
    try:
        return int(fallback_port) if fallback_port else 80
    except (TypeError, ValueError):
        return 80


def _mdns_txt_value_is_image_path(val: str) -> bool:
    """True when a TXT value points at a PNG/JPEG (URL or path), as in V1."""
    v = val.strip()
    if not v or len(v) > 2048:
        return False
    lower = v.lower()
    path_part = lower.split("?", 1)[0].split("#", 1)[0]
    if not any(path_part.endswith(ext) for ext in _MDNS_IMAGE_SUFFIXES):
        return False
    if lower.startswith(("http://", "https://")):
        return True
    return "/" in v or v.startswith((".", "\\"))


def _mdns_icon_urls(metadata: dict, ip: str, port: int) -> list[str]:
    """Collect device icon URLs from mDNS metadata and per-service TXT (GTK / V1 parity)."""
    urls: list[str] = []
    seen: set[str] = set()
    base_port = _mdns_preferred_icon_base_port(metadata, port)

    def _push(raw_val: str) -> None:
        u = _normalize_icon_url(raw_val.strip(), ip, base_port)
        if u not in seen:
            seen.add(u)
            urls.append(u)

    for key in ("icon", "apple-touch-icon", "favicon"):
        raw = metadata.get(key)
        if isinstance(raw, str) and raw.strip():
            _push(raw)

    def _scan_txt(txt: dict) -> None:
        if not isinstance(txt, dict):
            return
        for key, val in txt.items():
            if not isinstance(val, str) or not val.strip():
                continue
            key_l = str(key).strip().lower()
            if key_l in _MDNS_ICON_TXT_KEYS or _mdns_txt_value_is_image_path(val):
                _push(val)

    services = metadata.get("services")
    if isinstance(services, list):
        for svc in services:
            if not isinstance(svc, dict):
                continue
            for key in ("icon", "apple-touch-icon"):
                raw = svc.get(key)
                if isinstance(raw, str) and raw.strip():
                    _push(raw)
            _scan_txt(svc.get("txt"))

    txt_top = metadata.get("txt")
    if isinstance(txt_top, dict):
        _scan_txt(txt_top)

    return urls


def canonical_remote_icon_url(icon_url: str) -> str:
    """Stable cache key: normalized scheme, host, port, path (GTK-compatible)."""
    u = icon_url.strip()
    try:
        p = urlparse(u)
    except ValueError:
        return u
    if not p.scheme or not p.hostname:
        return u
    scheme = (p.scheme or "").lower()
    host_txt = (p.hostname or "").lower().rstrip(".")
    port = p.port

    host_literal = host_txt
    try:
        parsed_ip = ipaddress.ip_address(host_txt)
        if isinstance(parsed_ip, ipaddress.IPv6Address):
            host_literal = f"[{parsed_ip.compressed}]"
    except ValueError:
        host_literal = host_txt

    omit_default = (scheme == "http" and port in {None, 80}) or (
        scheme == "https" and port in {None, 443}
    )
    if omit_default:
        netloc_final = host_literal
    elif port is not None:
        netloc_final = f"{host_literal}:{port}"
    else:
        netloc_final = host_literal

    path = p.path.strip() if p.path else ""
    if not path:
        path = "/"
    query = (p.query or "").strip()
    return urlunparse((scheme, netloc_final, path, "", query, ""))


def _should_rewrite_icon_hostname_to_lan_ip(hostname: str) -> bool:
    """Replace any non-literal host with the device IP (avoids mDNS / DNS timeouts)."""
    h = (hostname or "").strip().lower().rstrip(".")
    if not h:
        return False
    try:
        ipaddress.ip_address(h.strip("[]"))
        return False
    except ValueError:
        return True


def rewrite_remote_icon_url_to_device_ip(url: str, ip: str) -> str:
    ip_stripped = str(ip).strip()
    if not ip_stripped or ip_stripped in {"0.0.0.0", "::"}:
        return url.strip()
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url.strip()
    if p.scheme.lower() not in {"http", "https"} or not p.netloc:
        return url.strip()
    host = p.hostname
    if not host or not _should_rewrite_icon_hostname_to_lan_ip(host):
        return url.strip()
    try:
        dev_ip = ipaddress.ip_address(ip_stripped)
    except ValueError:
        return url.strip()
    port = p.port
    if isinstance(dev_ip, ipaddress.IPv6Address):
        netloc = f"[{ip_stripped}]"
        if port is not None:
            netloc += f":{port}"
    else:
        netloc = ip_stripped
        if port is not None:
            netloc += f":{port}"
    path = p.path or "/"
    return urlunparse((p.scheme.lower(), netloc, path, "", p.query, ""))


def remote_icon_digest(icon_url: str) -> str:
    return hashlib.sha256(icon_url.encode("utf-8")).hexdigest()


def remote_icon_payload_path(cache_key: str) -> Path:
    return _REMOTE_ICON_DISK_DIR / f"{remote_icon_digest(cache_key)}.payload"


def primary_fetch_pair_for_device(device: Device) -> tuple[str, str] | None:
    """Returns ``(fetch_url, disk_cache_key)`` for the preferred device icon."""
    raw = remote_icon_url_for_device(device)
    if not isinstance(raw, str) or not raw.strip():
        return None
    sip = str(device.ip).strip()
    fetch_url = rewrite_remote_icon_url_to_device_ip(raw.strip(), sip).strip()
    if not fetch_url:
        return None
    cache_key = canonical_remote_icon_url(fetch_url)
    if not cache_key:
        return None
    return fetch_url, cache_key


def _expand_cached_remote_icon_aliases(canonical_key: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    ck = (canonical_key or "").strip()
    if not ck:
        return out

    def add(u: str) -> None:
        if u and u not in seen:
            seen.add(u)
            out.append(u)

    add(ck)
    try:
        p = urlparse(ck)
    except ValueError:
        return out
    scheme = (p.scheme or "").lower()
    host_txt = (p.hostname or "")
    if not host_txt:
        return out
    port = p.port
    path = p.path or "/"
    query = p.query or ""

    try:
        parsed_ip = ipaddress.ip_address(host_txt.strip("[]"))
        if isinstance(parsed_ip, ipaddress.IPv6Address):
            host_literal = f"[{parsed_ip.compressed}]"
        else:
            host_literal = host_txt
    except ValueError:
        host_literal = host_txt

    def build(netloc_piece: str) -> str:
        return urlunparse((scheme, netloc_piece, path, "", query, ""))

    if scheme == "http" and port in {None, 80}:
        add(build(f"{host_literal}:80"))
    elif scheme == "https" and port in {None, 443}:
        add(build(f"{host_literal}:443"))
    return out


def iter_remote_icon_disk_keys(device: Device) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    sip = str(device.ip).strip()

    def push_logical_url(candidate: str) -> None:
        if not isinstance(candidate, str) or not candidate.strip():
            return
        base = canonical_remote_icon_url(candidate.strip())
        for alias in _expand_cached_remote_icon_aliases(base):
            if alias not in seen:
                seen.add(alias)
                keys.append(alias)

    row = _load_index().get(sip)
    if row:
        remembered = row.get("canonical_url")
        if isinstance(remembered, str) and remembered.strip():
            push_logical_url(remembered)

    raw = remote_icon_url_for_device(device)
    if raw:
        push_logical_url(rewrite_remote_icon_url_to_device_ip(raw, sip))
        push_logical_url(raw)
    return keys


def load_remote_icon_payload_bytes(cache_key: str) -> bytes | None:
    path = remote_icon_payload_path(cache_key)
    if path.is_file():
        try:
            return path.read_bytes()
        except OSError:
            pass
    return None


def load_remote_icon_payload_for_device(device: Device) -> bytes | None:
    sip = str(device.ip).strip()
    row = _load_index().get(sip)
    if row:
        sha = row.get("payload_sha256")
        if isinstance(sha, str) and len(sha) == 64:
            sl = sha.lower()
            if all(c in "0123456789abcdef" for c in sl):
                path = _REMOTE_ICON_DISK_DIR / f"{sl}.payload"
                if path.is_file():
                    try:
                        return path.read_bytes()
                    except OSError:
                        pass
    for key in iter_remote_icon_disk_keys(device):
        data = load_remote_icon_payload_bytes(key)
        if data:
            return data
    return None


def save_remote_icon_payload(cache_key: str, data: bytes) -> None:
    if not data:
        return
    path = remote_icon_payload_path(cache_key)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass


def persist_remote_icon_index_entry(sip: str, canonical_url: str) -> None:
    if not sip or not isinstance(canonical_url, str) or not canonical_url.strip():
        return
    canon = canonical_remote_icon_url(canonical_url.strip())
    sha = remote_icon_digest(canon)
    hosts = dict(_load_index())
    prev = hosts.get(sip.strip())
    if prev and prev.get("canonical_url") == canon and prev.get("payload_sha256") == sha:
        return
    hosts[sip.strip()] = {"canonical_url": canon, "payload_sha256": sha}
    global _index_cache
    _index_cache = hosts
    try:
        _REMOTE_ICON_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        blob = {
            "version": 2,
            "hosts": dict(sorted(hosts.items())),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = _REMOTE_ICON_INDEX_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(blob, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, _REMOTE_ICON_INDEX_PATH)
    except OSError:
        pass


def urlopen_remote_icon(url: str):
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    ctx = None
    if scheme == "https":
        allow_insecure = host.endswith(".local") or host.endswith(".lan")
        if not allow_insecure and host:
            try:
                allow_insecure = ipaddress.ip_address(host).is_private
            except ValueError:
                pass
        if allow_insecure:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
    timeout = 4.5 if scheme == "https" else 2.0
    if ctx is not None:
        return urlopen(url, timeout=timeout, context=ctx)
    return urlopen(url, timeout=timeout)


def remote_icon_url_for_device(device: Device) -> str | None:
    metadata = device.metadata if isinstance(device.metadata, dict) else {}
    if device.source == "ssdp":
        xml_fields = metadata.get("xml_fields")
        if not isinstance(xml_fields, dict):
            return None
        icon_url = xml_fields.get("iconURL")
        if isinstance(icon_url, str) and icon_url.strip():
            return _normalize_icon_url(
                icon_url.strip(), device.ip, device.port or 80
            )
    if device.source == "mdns":
        for url in _mdns_icon_urls(metadata, device.ip, device.port):
            if url:
                return url
    return None


def bundle_has_device_icon_source(bundle: DeviceBundle) -> bool:
    for dev in (
        bundle.ssdp_device,
        bundle.wsdd_device,
        bundle.wsd_device,
        bundle.nmb_device,
        bundle.mdns_device,
    ):
        if dev is None:
            continue
        if remote_icon_url_for_device(dev):
            return True
        if cached_remote_icon_url_for_host(dev.ip):
            return True
    return False


def bundle_provided_icon_display(bundle: DeviceBundle) -> str | None:
    for dev in (
        bundle.ssdp_device,
        bundle.wsdd_device,
        bundle.wsd_device,
        bundle.nmb_device,
        bundle.mdns_device,
    ):
        if dev is None:
            continue
        url = remote_icon_url_for_device(dev)
        if url:
            return url
        cached = cached_remote_icon_url_for_host(dev.ip)
        if cached:
            return cached
    return None
