# File discovery_cache.py for NetNeighbor version 2.0.1
# Internal version : 2.0.1 date: 2026-05-26 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Persistence helpers for volatile discovery cache data."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_CACHE_DIR = Path.home() / ".cache" / "netneighbor"
_CACHE_FILE = _CACHE_DIR / "discovery-cache.json"

# Maximum age of a cache entry before it is purged from disk.
# Must be >= the per-protocol write TTL (ssdp.py: _PROFILE_CACHE_DISK_TTL_SECONDS = 24 h)
# so that entries written by one session are still readable the next day.
CACHE_MAX_AGE_HOURS: int = 48


def _parse_iso(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _is_stale(updated_at_raw: str | None, threshold: datetime) -> bool:
    """Return True if the entry's updated_at is older than *threshold*."""
    dt = _parse_iso(updated_at_raw)
    if dt is None:
        return False  # no parseable timestamp → keep (safer than silently dropping)
    return dt < threshold


def _purge_stale_section(section: Any, threshold: datetime) -> Any:
    """Remove entries with an updated_at older than *threshold* from a cache section dict."""
    if not isinstance(section, dict):
        return section
    entries = section.get("entries")
    if not isinstance(entries, dict):
        return section
    kept = {
        k: v
        for k, v in entries.items()
        if not (isinstance(v, dict) and _is_stale(v.get("updated_at"), threshold))
    }
    if len(kept) == len(entries):
        return section
    result = dict(section)
    result["entries"] = kept
    return result


def load_discovery_cache() -> dict[str, Any]:
    try:
        content = _CACHE_FILE.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def save_wsd_device_cache(device_key: str, row: dict[str, Any]) -> None:
    """Persist a WSD device row so restarts can pre-populate it immediately."""
    entry = dict(row)
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_discovery_cache({"wsd_device_cache": {"entries": {device_key: entry}}})


def save_nmb_name_cache(ip: str, name: str, mac: str | None = None) -> None:
    """Persist an NMB/DNS-resolved hostname so restarts can pre-populate it immediately."""
    now = datetime.now(timezone.utc).isoformat()
    entry: dict[str, Any] = {"name": name, "updated_at": now}
    if mac:
        entry["mac"] = mac
    save_discovery_cache({"nmb_name_cache": {"entries": {ip: entry}}})


def _host_from_urls(row: dict) -> str | None:
    """Extract a hostname/IP from SSDP profile-cache URLs."""
    urls: list[str] = []
    loc = row.get("ssdp_location")
    if isinstance(loc, str) and loc.strip():
        urls.append(loc.strip())
    url = row.get("url")
    if isinstance(url, str) and url.strip():
        urls.append(url.strip())
    xf = row.get("xml_fields")
    if isinstance(xf, dict):
        pres = xf.get("presentationURL")
        if isinstance(pres, str) and pres.strip():
            urls.append(pres.strip())
    for u in urls:
        try:
            host = urlparse(u).hostname
        except ValueError:
            continue
        if host:
            return str(host).strip()
    return None


def purge_device_from_discovery_cache(ips: set[str]) -> None:
    """Remove all cache entries that belong to any of the given IP addresses."""
    if not ips:
        return
    try:
        existing = load_discovery_cache()
        if not existing:
            return
        changed = False
        for section_name, section in existing.items():
            if not isinstance(section, dict):
                continue
            entries = section.get("entries")
            if not isinstance(entries, dict):
                continue
            to_remove: set[str] = set()
            for key, val in entries.items():
                if key in ips:
                    # nmb_name_cache: key is the IP itself
                    to_remove.add(key)
                elif isinstance(val, dict) and str(val.get("ip", "") or "").strip() in ips:
                    # wsd_device_cache: key is device key, val has an "ip" field
                    to_remove.add(key)
                elif section_name == "ssdp_profile_cache" and isinstance(val, dict):
                    # ssdp_profile_cache: keys are UUIDs/USNs; resolve IP from URLs.
                    host = _host_from_urls(val)
                    if host and host in ips:
                        to_remove.add(key)
            if to_remove:
                for key in to_remove:
                    del entries[key]
                changed = True
        if changed:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _CACHE_FILE.write_text(
                json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8"
            )
    except OSError:
        return


def save_discovery_cache(
    cache_data: dict[str, Any],
    max_age_hours: int = CACHE_MAX_AGE_HOURS,
) -> None:
    """Merge *cache_data* into the on-disk cache and purge entries older than *max_age_hours*."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        try:
            if _CACHE_FILE.exists():
                parsed = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    existing = parsed
        except (OSError, json.JSONDecodeError):
            existing = {}

        merged = dict(existing)
        merged.update(cache_data)

        # Purge stale entries from every section that holds an "entries" sub-dict.
        # This removes entries that were once written but have not been refreshed
        # within the retention window — they survive the merge but should not stay forever.
        threshold = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        for key in list(merged.keys()):
            merged[key] = _purge_stale_section(merged[key], threshold)

        _CACHE_FILE.write_text(
            json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8"
        )
    except OSError:
        # Keep UI responsive even if cache path is unavailable.
        return
