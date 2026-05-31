# File mdns_rules.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Load configurable mDNS heuristic rules (`config/mdns_rules.json`)."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from utils.user_config_overlay import merge_mdns_rules_overlays

_LOG = logging.getLogger(__name__)
_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "mdns_rules.json"


# The bundled config/mdns_rules.json is the single source of truth for the rules. This empty
# skeleton is only a graceful degraded fallback if that file is missing/corrupt — startup runs
# utils.config_integrity, which shows a "corrupted installation" dialog in that case, so the
# fallback below should never be reached in a healthy install.
_EMPTY_RULES: dict = {"summary_from_txt": [], "type_rules": []}


def _normalized_rule_dict(raw: object) -> dict:
    """Coerce a payload into a well-formed rules dict (missing keys filled with empty lists)."""
    if isinstance(raw, dict):
        merged = dict(_EMPTY_RULES)
        merged.update(raw)
        if not isinstance(merged.get("summary_from_txt"), list):
            merged["summary_from_txt"] = []
        if not isinstance(merged.get("type_rules"), list):
            merged["type_rules"] = []
        return merged
    return dict(_EMPTY_RULES)


def load_mdns_rules() -> dict:
    """Return rules from config/mdns_rules.json (single source); user overlay merged from ~/.config.

    A missing/corrupt bundled file degrades to empty rules — startup integrity check surfaces the
    "corrupted installation" message to the user (see utils.config_integrity).
    """
    try:
        data = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _LOG.warning("mDNS rules file missing at %s — degraded (no rules)", _RULES_PATH)
        data = dict(_EMPTY_RULES)
    except (json.JSONDecodeError, OSError) as exc:
        _LOG.warning("Invalid mDNS rules file %s: %s — degraded (no rules)", _RULES_PATH, exc)
        data = dict(_EMPTY_RULES)
    merged = merge_mdns_rules_overlays(_normalized_rule_dict(data))
    return _normalized_rule_dict(merged)


@lru_cache(maxsize=1)
def cached_mdns_rules() -> dict:
    """Rules loaded once per process (edit file + restart app to pick up changes)."""
    return load_mdns_rules()


def reload_mdns_rules_for_tests() -> None:
    cached_mdns_rules.cache_clear()


def summary_field_labels_norm(rules: dict | None = None) -> frozenset[str]:
    """Normalized detail labels emitted from summary_from_txt (for SSDP+mDNS merge)."""
    r = rules if isinstance(rules, dict) else cached_mdns_rules()
    norms: list[str] = []
    seq = r.get("summary_from_txt") or []
    if isinstance(seq, list):
        for row in seq:
            if isinstance(row, dict):
                lbl = row.get("label")
                if isinstance(lbl, str) and lbl.strip():
                    norms.append(lbl.strip().lower())
    return frozenset(norms)


def _norm_txt_key_lookup(key: str) -> str:
    return str(key).strip().lower()


def collect_first_txt_field(metadata: dict, key_candidates: list[str]) -> str:
    """First non-empty value across top-level TXT and each service TXT, keyed by aliases (case-insensitive)."""

    lookup_order: list[str] = []
    seen: set[str] = set()
    for k in key_candidates:
        nk = _norm_txt_key_lookup(k)
        if nk not in seen:
            seen.add(nk)
            lookup_order.append(nk)

    def _consume_txt_dict(txt_blob: dict) -> str | None:
        if not isinstance(txt_blob, dict):
            return None
        for lk in lookup_order:
            for kk, vv in txt_blob.items():
                if _norm_txt_key_lookup(kk) != lk:
                    continue
                s = vv.strip() if isinstance(vv, str) else str(vv).strip()
                if s:
                    return s
        return None

    txt_top = metadata.get("txt")
    got = _consume_txt_dict(txt_top) if isinstance(txt_top, dict) else None
    if got:
        return got

    services = metadata.get("services")
    if isinstance(services, list):
        for svc in services:
            if not isinstance(svc, dict):
                continue
            stxt = svc.get("txt")
            got_inner = _consume_txt_dict(stxt) if isinstance(stxt, dict) else None
            if got_inner:
                return got_inner
    return ""


def summary_rows_from_rules(metadata: dict, rules: dict | None = None) -> list[tuple[str, str]]:
    """Produce (detail label, raw value) rows for the first-tab mDNS summary from rules."""
    r = rules if isinstance(rules, dict) else cached_mdns_rules()
    rows: list[tuple[str, str]] = []
    seq = r.get("summary_from_txt") or []
    if not isinstance(seq, list):
        return rows
    for row in seq:
        if not isinstance(row, dict):
            continue
        label = row.get("label")
        keys_raw = row.get("keys")
        if not isinstance(label, str) or not label.strip():
            continue
        keys: list[str] = []
        if isinstance(keys_raw, list):
            for k in keys_raw:
                if isinstance(k, str) and k.strip():
                    keys.append(k.strip())
        if not keys:
            continue
        value = collect_first_txt_field(metadata, keys)
        if value:
            rows.append((label.strip(), value))
    return rows


def evaluate_type_rules(haystack_lower: str, current_type: str, rules: dict | None = None) -> str:
    """Evaluate type_rules contains_any lists in order (first match wins); haystack already lowercased."""
    r = rules if isinstance(rules, dict) else cached_mdns_rules()
    seq = r.get("type_rules") or []
    if not isinstance(seq, list):
        return current_type
    for entry in seq:
        if not isinstance(entry, dict):
            continue
        needles = entry.get("contains_any")
        target = entry.get("type")
        if not isinstance(target, str) or not target.strip():
            continue
        if not isinstance(needles, list):
            continue
        lowered_needles = [str(n).lower() for n in needles if isinstance(n, str) and str(n).strip()]
        if not lowered_needles:
            continue
        if any(n in haystack_lower for n in lowered_needles):
            return target.strip().lower()
    return current_type
