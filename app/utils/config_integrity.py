# File config_integrity.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Startup integrity check for the bundled, non-defaultable config files.

These JSON files ship inside the application bundle and are the *single source of truth* for
device classification (no Python fallback duplicates them anymore). If one is missing or
unparseable the installation is corrupt — the app surfaces a "reinstall" dialog and exits
rather than silently running with no rules.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

_LOG = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# Bundled data files that have no meaningful runtime default. Tunables with code defaults
# (discovery.json, logging.json, …) are intentionally NOT listed — their absence is normal.
REQUIRED_CONFIG_FILES: tuple[str, ...] = (
    "device_types.json",
    "ssdp_rules.json",
    "mdns_rules.json",
    "wsd_rules.json",
)


def check_bundled_config_integrity() -> list[str]:
    """Return a list of human-readable problems for missing/corrupt required config files.

    Empty list ⇒ healthy install. Each entry is like ``"ssdp_rules.json: missing"``.
    """
    problems: list[str] = []
    for name in REQUIRED_CONFIG_FILES:
        path = _CONFIG_DIR / name
        if not path.is_file():
            problems.append(f"{name}: missing")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{name}: invalid JSON ({exc.msg}, line {exc.lineno})")
            continue
        except OSError as exc:
            problems.append(f"{name}: unreadable ({exc})")
            continue
        if not isinstance(data, dict):
            problems.append(f"{name}: not a JSON object")
    if problems:
        _LOG.error("Bundled config integrity check failed: %s", problems)
    return problems
