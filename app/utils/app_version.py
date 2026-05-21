# File app_version.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Application release version: single source in repository root ``VERSION``."""

from pathlib import Path


def get_app_version(default: str = "0.0.0") -> str:
    """Return the first non-empty, non-comment line from ``VERSION`` at project root."""
    root = Path(__file__).resolve().parent.parent
    path = root / "VERSION"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return default
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        return s
    return default
