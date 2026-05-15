# File user_config_overlay.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Merge bundled discovery JSON with optional user overlays in ~/.config/netneighbor."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

USER_CONFIG_DIR = Path.home() / ".config" / "netneighbor"
USER_DEVICE_TYPES_JSON = USER_CONFIG_DIR / "device_types.json"
USER_ICONS_JSON = USER_CONFIG_DIR / "icons.json"
USER_SSDP_RULES_JSON = USER_CONFIG_DIR / "ssdp_rules.json"
USER_MDNS_RULES_JSON = USER_CONFIG_DIR / "mdns_rules.json"


def optional_user_json(filepath: Path) -> dict[str, Any] | None:
    if not filepath.is_file():
        return None
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except OSError:
        _LOG.warning("Could not read user config overlay %s", filepath)
        return None
    except json.JSONDecodeError as exc:
        _LOG.warning("Invalid JSON overlay %s: %s — ignoring overlay", filepath, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def _deep_copy_json(obj: Any) -> Any:
    return json.loads(json.dumps(obj))


def merge_device_types_trees(shipped: dict[str, Any], user_overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge overlay into shipped: `mdns` / `ssdp` keyed maps merge per-service; shallow merge fallback."""
    if not isinstance(shipped, dict):
        shipped = {}

    merged: dict[str, Any] = _deep_copy_json(shipped)

    def _merge_string_key_maps(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
        out = dict(base)
        for key, oval in overlay.items():
            if isinstance(key, str) and isinstance(oval, dict):
                prev = out.get(key)
                if isinstance(prev, dict):
                    out[key] = {**prev, **oval}
                else:
                    out[key] = dict(oval)
            elif isinstance(key, str):
                out[key] = oval
        return out

    u_mdns = user_overlay.get("mdns")
    if isinstance(u_mdns, dict):
        merged["mdns"] = _merge_string_key_maps(merged.get("mdns") or {}, u_mdns)

    u_ssdp = user_overlay.get("ssdp")
    if isinstance(u_ssdp, dict):
        merged["ssdp"] = _merge_string_key_maps(merged.get("ssdp") or {}, u_ssdp)

    u_fb = user_overlay.get("fallback")
    if isinstance(u_fb, dict):
        fb0 = merged.get("fallback") if isinstance(merged.get("fallback"), dict) else {}
        merged["fallback"] = {**fb0, **u_fb}

    return merged


def merge_ssdp_rules_overlays(bundled: dict[str, Any]) -> dict[str, Any]:
    """User ~/.config/netneighbor/ssdp_rules.json: merge name/information dicts; prepend type_rules."""

    overlay = optional_user_json(USER_SSDP_RULES_JSON)
    if overlay is None or overlay == {}:
        return bundled

    out = _deep_copy_json(bundled)

    name_u = overlay.get("name_rules")
    if isinstance(name_u, dict):
        base_nm = dict(out.get("name_rules") or {})
        base_nm.update(name_u)
        out["name_rules"] = base_nm

    info_u = overlay.get("information_rules")
    if isinstance(info_u, dict):
        base_info = dict(out.get("information_rules") or {})
        base_info.update(info_u)
        out["information_rules"] = base_info

    tu = overlay.get("type_rules")
    if isinstance(tu, list):
        base_tr = list(out.get("type_rules") or [])
        out["type_rules"] = [r for r in tu if isinstance(r, dict)] + base_tr

    _LOG.debug("Merged SSDP rules with user overlay %s", USER_SSDP_RULES_JSON)
    return out


def _merge_summary_txt_rows(user_rows: list[Any], bundled_rows: list[Any]) -> list[dict[str, Any]]:
    """Merge summary_from_txt: labels from user rows first (order preserved); bundled-only labels follow."""

    acc: dict[str, dict[str, Any]] = {}

    def _norm_label(lbl: str) -> str:
        return lbl.strip().lower()

    def _union_keys_user_then_bundled(user_keys: list[str], bundled_keys: list[str]) -> list[str]:
        seen: set[str] = set()
        out_keys: list[str] = []
        for bucket in (user_keys, bundled_keys):
            for k in bucket:
                lk = k.strip().lower()
                if lk in seen:
                    continue
                seen.add(lk)
                out_keys.append(k)
        return out_keys

    def _accumulate(rows: list[Any], *, bundled_phase: bool) -> None:
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_lbl = row.get("label")
            keys_raw = row.get("keys")
            if not isinstance(raw_lbl, str) or not raw_lbl.strip():
                continue
            nk = _norm_label(raw_lbl)
            lbl_display = raw_lbl.strip()

            ks: list[str] = []
            if isinstance(keys_raw, list):
                for k in keys_raw:
                    if isinstance(k, str) and k.strip():
                        ks.append(k.strip())

            if nk not in acc:
                acc[nk] = {"label": lbl_display, "keys": list(ks)}
            else:
                acc[nk]["keys"] = _union_keys_user_then_bundled(acc[nk]["keys"], ks)

    order: list[str] = []

    def _track(rows: list[Any]) -> None:
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            rl = row.get("label")
            if isinstance(rl, str) and rl.strip():
                nk = rl.strip().lower()
                if nk not in order:
                    order.append(nk)

    _accumulate(user_rows, bundled_phase=False)
    _accumulate(bundled_rows, bundled_phase=True)
    _track(user_rows or [])
    for row in bundled_rows or []:
        if not isinstance(row, dict):
            continue
        rl = row.get("label")
        if isinstance(rl, str) and rl.strip():
            nk = rl.strip().lower()
            if nk not in order:
                order.append(nk)

    return [{"label": acc[k]["label"], "keys": acc[k]["keys"]} for k in order if k in acc]


def merge_mdns_rules_overlays(bundled: dict[str, Any]) -> dict[str, Any]:
    """User ~/.config/netneighbor/mdns_rules.json merged with bundled/cached defaults."""

    overlay = optional_user_json(USER_MDNS_RULES_JSON)
    if overlay is None or overlay == {}:
        return bundled

    out = _deep_copy_json(bundled)

    su = overlay.get("summary_from_txt")
    bs = out.get("summary_from_txt")

    merged_summary: list[dict[str, Any]]
    if isinstance(su, list):
        merged_summary = _merge_summary_txt_rows(su, bs if isinstance(bs, list) else [])
    elif isinstance(bs, list):
        merged_summary = _merge_summary_txt_rows([], bs)
    else:
        merged_summary = []

    if merged_summary:
        out["summary_from_txt"] = merged_summary

    tu = overlay.get("type_rules")
    if isinstance(tu, list):
        base_tr = list(out.get("type_rules") or [])
        out["type_rules"] = [r for r in tu if isinstance(r, dict)] + base_tr

    _LOG.debug("Merged mDNS rules with user overlay %s", USER_MDNS_RULES_JSON)
    return out
