"""Icon resolution helpers."""

from pathlib import Path


def resolve_icon_path(icon_name: str | None) -> Path:
    base = Path(__file__).resolve().parent.parent / "assets" / "icons"
    if icon_name:
        candidate = base / icon_name
        if candidate.exists():
            return candidate
        alt_svg = candidate.with_suffix(".svg")
        if alt_svg.exists():
            return alt_svg
    unknown_png = base / "unknown.png"
    if unknown_png.exists():
        return unknown_png
    unknown_svg = base / "unknown.svg"
    if unknown_svg.exists():
        return unknown_svg
    return unknown_png


def resolve_app_icon_path() -> Path | None:
    base = Path(__file__).resolve().parent.parent / "assets" / "icons"
    for name in ("netneighbor.png", "netneighbor.svg", "app.png", "app.svg"):
        candidate = base / name
        if candidate.exists():
            return candidate
    return None
