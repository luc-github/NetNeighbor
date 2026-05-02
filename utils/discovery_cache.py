"""Persistence helpers for volatile discovery cache data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CACHE_DIR = Path.home() / ".cache" / "netneighbor"
_CACHE_FILE = _CACHE_DIR / "discovery-cache.json"


def load_discovery_cache() -> dict[str, Any]:
    try:
        content = _CACHE_FILE.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def save_discovery_cache(cache_data: dict[str, Any]) -> None:
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
        _CACHE_FILE.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        # Keep UI responsive even if cache path is unavailable.
        return

