"""Icon resolution helpers."""

from pathlib import Path


def resolve_icon_path(icon_name: str | None) -> Path:
    base = Path(__file__).resolve().parent.parent / "assets" / "icons"
    if icon_name:
        candidate = base / icon_name
        if candidate.exists():
            return candidate
    return base / "unknown.png"
