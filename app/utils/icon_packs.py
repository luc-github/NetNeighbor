# File icon_packs.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Icon pack discovery and resolution.

User-installed packs live in ``~/.config/netneighbor_icon_packs/{pack_id}/``,
a directory intentionally outside ``~/.config/netneighbor/`` so that
*Reset all application data* does not wipe them.

Each pack directory may optionally contain ``iconpack.json`` and ``preview.png``.
Icons are stored under ``{N}x{N}/`` **or** flat ``{N}/`` sub-directories so both
naming conventions are supported.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

_LOG = logging.getLogger("utils.icon_packs")

BUILTIN_PACK_ID = "builtin"

# Module-level cache — call invalidate_icon_pack_cache() after saving prefs.
_active_pack_id_cache: str | None = None


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def user_icon_packs_dir() -> Path:
    """``~/.config/netneighbor_icon_packs`` — survives *Reset all application data*."""
    return Path.home() / ".config" / "netneighbor_icon_packs"


def _builtin_pack_root() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "icons" / "netneighbor"


def _assets_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class IconPackInfo:
    pack_id: str
    name: str
    owner: str = ""
    version: str = ""
    date: str = ""
    resolutions: str = ""
    repository: str = ""
    license: str = ""
    root: Path = field(default_factory=Path)
    preview_path: Path | None = None


# ---------------------------------------------------------------------------
# Size helpers (supports both {N}x{N}/ and flat {N}/ naming)
# ---------------------------------------------------------------------------

def scan_pack_sizes(pack_root: Path) -> tuple[int, ...]:
    """Sorted distinct icon sizes available in ``pack_root``.

    Supports both ``{N}x{N}/`` and flat ``{N}/`` directory naming.
    """
    sizes: set[int] = set()
    try:
        for p in pack_root.iterdir():
            if not p.is_dir():
                continue
            name = p.name
            if "x" in name:
                a, _, b = name.partition("x")
                if a.isdigit() and b.isdigit() and a == b:
                    n = int(a)
                    if 1 <= n <= 4096:
                        sizes.add(n)
            elif name.isdigit():
                n = int(name)
                if 1 <= n <= 4096:
                    sizes.add(n)
    except OSError:
        pass
    return tuple(sorted(sizes))


def find_icon_in_pack(pack_root: Path, sz: int, stem: str) -> Path | None:
    """Return path to ``{stem}.png`` at size ``sz`` in ``pack_root``, or ``None``.

    Checks ``{N}x{N}/`` first, then flat ``{N}/``.
    """
    for dir_name in (f"{sz}x{sz}", str(sz)):
        p = pack_root / dir_name / f"{stem}.png"
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------------------
# Pack discovery
# ---------------------------------------------------------------------------

def _find_meta_file(directory: Path) -> Path | None:
    """Return ``iconpack.json`` in ``directory`` if it exists, else ``None``."""
    p = directory / "iconpack.json"
    return p if p.is_file() else None


def _parse_meta_text(text: str) -> dict | None:
    """Parse JSON or Python-dict-literal metadata (single-quote files are accepted)."""
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    try:
        import ast
        result = ast.literal_eval(text.strip())
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    return None


def _load_pack_meta(meta_path: Path, pack_id: str, root: Path) -> IconPackInfo | None:
    try:
        raw = _parse_meta_text(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if raw is None:
        return None
    name = str(raw.get("name", pack_id)).strip() or pack_id
    return IconPackInfo(
        pack_id=pack_id,
        name=name,
        owner=str(raw.get("owner", "")),
        version=str(raw.get("version", "")),
        date=str(raw.get("date", "")),
        resolutions=str(raw.get("resolutions", "")),
        repository=str(raw.get("repository", "")),
        license=str(raw.get("license", "")),
        root=root,
    )


def load_available_packs() -> list[IconPackInfo]:
    """Return all available icon packs: built-in first, then user-installed (sorted by name)."""
    packs: list[IconPackInfo] = []

    # Built-in pack
    assets = _assets_dir()
    builtin_root = _builtin_pack_root()
    meta_path = _find_meta_file(assets)
    if meta_path is not None:
        info = _load_pack_meta(meta_path, BUILTIN_PACK_ID, builtin_root)
        if info is not None:
            # Preview: check pack root first, then assets/
            for preview_candidate in (builtin_root / "preview.png", assets / "iconpack_preview.png"):
                if preview_candidate.is_file():
                    info.preview_path = preview_candidate
                    break
            packs.append(info)
    if not packs:
        from gettext import gettext as _
        packs.append(IconPackInfo(
            pack_id=BUILTIN_PACK_ID,
            name=_("NetNeighbor (built-in)"),
            root=builtin_root,
        ))

    # User-installed packs (iconpack.json is optional)
    udir = user_icon_packs_dir()
    if udir.is_dir():
        user_packs: list[IconPackInfo] = []
        try:
            for subdir in sorted(udir.iterdir()):
                if not subdir.is_dir():
                    continue
                meta = _find_meta_file(subdir)
                if meta is not None:
                    info = _load_pack_meta(meta, subdir.name, subdir)
                    if info is None:
                        continue
                else:
                    info = IconPackInfo(pack_id=subdir.name, name=subdir.name, root=subdir)
                preview = subdir / "preview.png"
                info.preview_path = preview if preview.is_file() else None
                user_packs.append(info)
        except OSError:
            pass
        user_packs.sort(key=lambda p: p.name.lower())
        packs.extend(user_packs)

    return packs


# ---------------------------------------------------------------------------
# Active pack resolution (cached)
# ---------------------------------------------------------------------------

def active_icon_pack_id() -> str:
    """Active icon pack ID from ``ui_prefs.json`` (cached).

    Call :func:`invalidate_icon_pack_cache` after saving a new value to prefs.
    """
    global _active_pack_id_cache
    if _active_pack_id_cache is not None:
        return _active_pack_id_cache
    try:
        from utils.ui_prefs import load_ui_preferences
        prefs = load_ui_preferences()
        val = prefs.get("icon_pack", BUILTIN_PACK_ID)
        result = str(val).strip() if isinstance(val, str) and val.strip() else BUILTIN_PACK_ID
    except Exception:
        result = BUILTIN_PACK_ID
    _active_pack_id_cache = result
    return result


def invalidate_icon_pack_cache() -> None:
    """Invalidate the cached active pack ID (call after saving prefs)."""
    global _active_pack_id_cache
    _active_pack_id_cache = None


def active_icon_pack_root() -> Path:
    """Root directory for the active icon pack (falls back to built-in if not found)."""
    pack_id = active_icon_pack_id()
    if not pack_id or pack_id == BUILTIN_PACK_ID:
        return _builtin_pack_root()
    candidate = user_icon_packs_dir() / pack_id
    if candidate.is_dir():
        return candidate
    _LOG.warning("Icon pack '%s' not found, falling back to built-in", pack_id)
    return _builtin_pack_root()
