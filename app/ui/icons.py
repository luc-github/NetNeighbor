# File icons.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Icon resolution helpers."""

import sys
from pathlib import Path

_bfd_sizes_cache: tuple[int, ...] | None = None
_bfd_sizes_mtime: float | None = None


def resolve_asset_icon_file(icon_name: str | None) -> Path | None:
    """Return a path under ``assets/icons`` only when that file exists.

    Unlike :func:`resolve_icon_path`, this does **not** fall back to ``unknown.png``,
    so callers can detect a missing bundled asset and use type-based system icons instead.
    """
    if not icon_name:
        return None
    base = Path(__file__).resolve().parent.parent / "assets" / "icons"
    name = Path(str(icon_name).strip()).name
    if not name:
        return None
    candidate = base / name
    if candidate.is_file():
        return candidate
    alt_svg = candidate.with_suffix(".svg")
    if alt_svg.is_file():
        return alt_svg
    return None


def _bundled_freedesktop_root() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "icons" / "netneighbor"


def bundled_freedesktop_png_side_sizes() -> tuple[int, ...]:
    """Available icon sizes under ``assets/icons/netneighbor``, sorted ascending.

    Supports both flat ``{N}/`` and square ``{N}x{N}/`` subdirectory naming.
    Discovered at runtime (cached until the directory mtime changes).
    """
    global _bfd_sizes_cache, _bfd_sizes_mtime
    root = _bundled_freedesktop_root()
    if not root.is_dir():
        return ()
    try:
        mtime = root.stat().st_mtime
    except OSError:
        return ()
    if _bfd_sizes_cache is not None and _bfd_sizes_mtime == mtime:
        return _bfd_sizes_cache
    from utils.icon_packs import scan_pack_sizes
    out = tuple(sorted(scan_pack_sizes(root)))
    _bfd_sizes_cache = out
    _bfd_sizes_mtime = mtime
    return out


def user_custom_icons_dir() -> Path:
    """``~/.config/netneighbor/custom_icons`` (PNG overrides, shared GTK/Qt)."""
    return Path.home() / ".config" / "netneighbor" / "custom_icons"


def iter_bundled_freedesktop_choice_basenames() -> list[str]:
    """Unique ``*.png`` stems under any size subfolder of ``assets/icons/netneighbor`` (sorted)."""
    root = _bundled_freedesktop_root()
    names: set[str] = set()
    try:
        for sz in bundled_freedesktop_png_side_sizes():
            for fmt in (f"{sz}x{sz}", str(sz)):
                d = root / fmt
                if d.is_dir():
                    for p in d.glob("*.png"):
                        names.add(p.stem)
                    break
    except OSError:
        return []
    return sorted(names)


def resolve_persisted_icon_id_to_path(
    icon_id: str | None,
    active_pack_root: Path | None = None,
) -> Path | None:
    """Resolve a stored icon id (``bundled:stem``, ``custom:file.png``, ``builtin:file.png``).

    For ``bundled:`` ids, *active_pack_root* is tried first; falls back to the built-in pack.
    """
    if not icon_id or not isinstance(icon_id, str):
        return None
    s = icon_id.strip()
    if not s:
        return None
    if ":" in s:
        prefix, rest = s.split(":", 1)
        rest = rest.strip()
        name = Path(rest).name
        if not name:
            return None
        pfx = prefix.strip().lower()
        if pfx == "bundled":
            stem = Path(name).stem
            return _resolve_picker_preview(stem, active_pack_root)
        if pfx == "custom":
            p = user_custom_icons_dir() / name
            return p if p.is_file() else None
        if pfx == "builtin":
            return resolve_asset_icon_file(name)
    return resolve_asset_icon_file(Path(s).name)


def resolve_bundled_freedesktop_icon(icon_name: str | None) -> Path | None:
    """One file under ``assets/icons/netneighbor`` for a Freedesktop basename.

    Prefers the **largest** existing ``{N}x{N}/{name}.png``, then flat ``{name}.png`` / ``{name}.svg``.
    For Qt multi-resolution icons, ``utils.qt_device_icons`` registers every ``NxN`` size found.
    """
    if not icon_name:
        return None
    raw = str(icon_name).strip()
    stem = Path(raw).name
    if not stem or stem != raw:
        return None
    from utils.icon_packs import find_icon_in_pack
    base = _bundled_freedesktop_root()
    for sz in reversed(bundled_freedesktop_png_side_sizes()):
        candidate = find_icon_in_pack(base, sz, stem)
        if candidate is not None:
            return candidate
    for ext in (".svg", ".png"):
        candidate = base / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
    return None


def _resolve_picker_preview(stem: str, active_pack_root: Path | None) -> Path | None:
    """Return best preview path for *stem*: active pack → built-in fallback."""
    if active_pack_root is not None:
        from utils.icon_packs import find_icon_in_pack, scan_pack_sizes
        for sz in reversed(scan_pack_sizes(active_pack_root)):
            candidate = find_icon_in_pack(active_pack_root, sz, stem)
            if candidate is not None:
                return candidate
    return resolve_bundled_freedesktop_icon(stem)


def iter_icon_picker_entries(
    preferred_type: str | None,
    active_pack_root: Path | None = None,
) -> list[tuple[str, str, Path | None]]:
    """Rows for GTK/Qt icon pickers: ``(icon_id, label, preview_path)``.

    ``preview_path`` may be ``None`` only if a file disappeared between scan and open.
    Icons are previewed from *active_pack_root* when provided, falling back to built-in.
    """
    from gettext import gettext as _

    from utils.type_icon_config import type_icon_basenames_for_slug

    rows: list[tuple[str, str, Path | None]] = []
    for stem in iter_bundled_freedesktop_choice_basenames():
        p = _resolve_picker_preview(stem, active_pack_root)
        rows.append((f"bundled:{stem}", f"{stem} ({_('App icon pack')})", p))
    cdir = user_custom_icons_dir()
    if cdir.is_dir():
        for icon_path in sorted(cdir.glob("*.png")):
            rows.append(
                (
                    f"custom:{icon_path.name}",
                    f"{icon_path.name} ({_('Custom')})",
                    icon_path if icon_path.is_file() else None,
                )
            )
    slug = (preferred_type or "").strip().lower()
    preferred: set[str] = set(type_icon_basenames_for_slug(slug)) if slug else set()

    def _sort_key(t: tuple[str, str, Path | None]) -> tuple[int, str]:
        icon_id, label, _p = t
        stem = ""
        if icon_id.startswith("bundled:"):
            stem = icon_id.split(":", 1)[1]
        is_pref = 0 if stem in preferred else 1
        return (is_pref, label.lower())

    rows.sort(key=_sort_key)
    return rows


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


def resolve_app_icon_paths_in_order() -> list[Path]:
    """Bundled app icon paths in preference order.

    On Windows, a multi-resolution ``.ico`` is preferred for the taskbar when running under
    ``python.exe``; place ``assets/icons/netneighbor.ico`` (or ``app.ico``) if you have one.
    """
    root = Path(__file__).resolve().parent.parent
    out: list[Path] = []
    icons_dir = root / "assets" / "icons"
    if sys.platform == "win32":
        for name in ("netneighbor.ico", "app.ico", "netneighbor_icon.ico"):
            candidate = icons_dir / name
            if candidate.is_file():
                out.append(candidate)
    svg_dir = root / "assets" / "svg"
    for name in ("netneighbor_icon.svg", "netneighbor.svg"):
        candidate = svg_dir / name
        if candidate.is_file():
            out.append(candidate)
    base = icons_dir
    for name in ("logo.png", "logo.svg", "netneighbor.png", "netneighbor.svg", "app.png", "app.svg"):
        candidate = base / name
        if candidate.exists():
            out.append(candidate)
    return out


def resolve_app_icon_path() -> Path | None:
    """Return the window/taskbar icon path (``.ico`` on Windows if present, else SVG / PNG)."""
    paths = resolve_app_icon_paths_in_order()
    return paths[0] if paths else None


def resolve_app_logo_path() -> Path | None:
    """Return the high-res logo path used in the About dialog (same preference order)."""
    return resolve_app_icon_path()
