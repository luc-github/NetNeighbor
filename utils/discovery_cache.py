# File discovery_cache.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Persistence helpers for volatile discovery cache data."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
