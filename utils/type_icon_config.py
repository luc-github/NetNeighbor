# File type_icon_config.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Load per-type default icon basenames from ``config/icons.json`` (+ optional user overlay)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from utils.user_config_overlay import USER_ICONS_JSON, optional_user_json

_LOG = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_SHIPPED_ICONS_JSON = _ROOT / "config" / "icons.json"

_cache_map: dict[str, list[str]] | None = None
_cache_sig: tuple[float, float] | None = None


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return -1.0


def _normalize_icon_basename(name: str) -> str:
    """Strip optional trailing ``.png`` so JSON may use either ``printer`` or ``printer.png``."""
    t = name.strip()
    if t.lower().endswith(".png"):
        t = t[:-4].strip()
    return t


def _parse_types_object(raw: dict[str, Any]) -> dict[str, list[str]]:
    t = raw.get("types")
    if not isinstance(t, dict):
        return {}
    out: dict[str, list[str]] = {}
    for k, v in t.items():
        if not isinstance(k, str) or not k.strip():
            continue
        slug = k.strip().lower()
        if isinstance(v, list):
            names = [str(x).strip() for x in v if isinstance(x, (str, int)) and str(x).strip()]
        elif isinstance(v, str) and v.strip():
            names = [v.strip()]
        else:
            continue
        names = [_normalize_icon_basename(x) for x in names]
        names = [x for x in names if x]
        if names:
            out[slug] = names
    return out


def _merge_type_maps(base: dict[str, list[str]], overlay: dict[str, Any] | None) -> dict[str, list[str]]:
    if not overlay:
        return base
    ot = overlay.get("types")
    if not isinstance(ot, dict):
        return base
    out = dict(base)
    for k, v in ot.items():
        if not isinstance(k, str) or not k.strip():
            continue
        slug = k.strip().lower()
        if isinstance(v, list):
            names = [str(x).strip() for x in v if isinstance(x, (str, int)) and str(x).strip()]
        elif isinstance(v, str) and v.strip():
            names = [v.strip()]
        else:
            continue
        names = [_normalize_icon_basename(x) for x in names]
        names = [x for x in names if x]
        if names:
            out[slug] = names
    return out


def _load_shipped_types() -> dict[str, list[str]]:
    if not _SHIPPED_ICONS_JSON.is_file():
        _LOG.warning("Missing shipped type icons config %s", _SHIPPED_ICONS_JSON)
        return {}
    try:
        data = json.loads(_SHIPPED_ICONS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("Invalid shipped icons.json: %s", exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return _parse_types_object(data)


def load_type_icon_basenames_by_slug(*, force_reload: bool = False) -> dict[str, list[str]]:
    """Merged ``slug -> [basename, …]`` from ``config/icons.json`` and ``~/.config/netneighbor/icons.json``."""
    global _cache_map, _cache_sig
    mt_ship = _mtime(_SHIPPED_ICONS_JSON)
    mt_user = _mtime(USER_ICONS_JSON)
    sig = (mt_ship, mt_user)
    if not force_reload and _cache_map is not None and _cache_sig == sig:
        return _cache_map

    shipped = _load_shipped_types()
    user_doc = optional_user_json(USER_ICONS_JSON)
    merged = _merge_type_maps(shipped, user_doc)
    if "unknown" not in merged:
        merged["unknown"] = ["computer", "network-workgroup", "network-server"]

    _cache_map = merged
    _cache_sig = sig
    return merged


def type_icon_basenames_for_slug(device_type_slug: str | None) -> list[str]:
    """Ordered basenames for ``bundled-freedesktop`` lookup (falls back to ``unknown``)."""
    m = load_type_icon_basenames_by_slug()
    slug = (device_type_slug or "unknown").strip().lower() or "unknown"
    if slug in m:
        return list(m[slug])
    return list(m.get("unknown", ["computer", "network-workgroup", "network-server"]))


def device_type_icon_slugs() -> tuple[str, ...]:
    """Stable sorted list of type slugs defined in the merged icon map."""
    return tuple(sorted(load_type_icon_basenames_by_slug().keys()))
