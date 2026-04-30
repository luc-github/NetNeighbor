"""Persistence helpers for UI preferences."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path.home() / ".config" / "netneighbor"
_PREFS_FILE = _CONFIG_DIR / "ui_prefs.json"


def load_ui_preferences() -> dict[str, Any]:
    try:
        content = _PREFS_FILE.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def save_ui_preferences(preferences: dict[str, Any]) -> None:
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _PREFS_FILE.write_text(json.dumps(preferences, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        # Keep UI responsive even if config path is unavailable.
        return

