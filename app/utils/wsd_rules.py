# File wsd_rules.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Load configurable WSD QName classification rules (`config/wsd_rules.json`).

WSD classifies a host from the DPWS service QNames it advertises (namespace + localname),
not from free text — so the rule schema matches on structured QName attributes rather than
substrings. See `config/wsd_rules.json` for the documented schema.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from utils.user_config_overlay import merge_wsd_rules_overlays

_LOG = logging.getLogger(__name__)
_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "wsd_rules.json"

# The bundled config/wsd_rules.json is the single source of truth. This empty skeleton is only a
# graceful degraded fallback if that file is missing/corrupt (startup utils.config_integrity then
# shows a "corrupted installation" dialog). "computer" stays the default so WSD hosts still appear.
_EMPTY_RULES: dict = {"qname_rules": [], "precedence": [], "default": "computer"}

_PREDICATE_OPS = ("local_eq", "local_endswith", "local_contains_any", "ns_contains")


def _normalized_rule_dict(raw: object) -> dict:
    """Coerce a payload into a well-formed rules dict (missing keys filled with safe empties)."""
    if isinstance(raw, dict):
        merged = dict(_EMPTY_RULES)
        merged.update(raw)
        if not isinstance(merged.get("qname_rules"), list):
            merged["qname_rules"] = []
        if not isinstance(merged.get("precedence"), list):
            merged["precedence"] = []
        if not isinstance(merged.get("default"), str) or not merged["default"].strip():
            merged["default"] = _EMPTY_RULES["default"]
        return merged
    return dict(_EMPTY_RULES)


def load_wsd_rules() -> dict:
    """Return rules from config/wsd_rules.json (single source); user overlay merged from ~/.config.

    A missing/corrupt bundled file degrades to empty rules (default type ``computer``) — startup
    integrity check surfaces the "corrupted installation" message (see utils.config_integrity).
    """
    try:
        data = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _LOG.warning("WSD rules file missing at %s — degraded (no rules)", _RULES_PATH)
        data = dict(_EMPTY_RULES)
    except (json.JSONDecodeError, OSError) as exc:
        _LOG.warning("Invalid WSD rules file %s: %s — degraded (no rules)", _RULES_PATH, exc)
        data = dict(_EMPTY_RULES)
    merged = merge_wsd_rules_overlays(_normalized_rule_dict(data))
    return _normalized_rule_dict(merged)


@lru_cache(maxsize=1)
def cached_wsd_rules() -> dict:
    """Rules loaded once per process (edit file + restart app to pick up changes)."""
    return load_wsd_rules()


def reload_wsd_rules_for_tests() -> None:
    cached_wsd_rules.cache_clear()


def _predicate_matches(pred: dict, ns: str, local: str) -> bool:
    """A predicate matches when every operator it declares matches (AND); empty predicate never matches."""
    if not isinstance(pred, dict) or not any(op in pred for op in _PREDICATE_OPS):
        return False
    local_eq = pred.get("local_eq")
    if isinstance(local_eq, list):
        if local not in {str(x).strip().lower() for x in local_eq if isinstance(x, str)}:
            return False
    local_endswith = pred.get("local_endswith")
    if isinstance(local_endswith, list):
        if not any(local.endswith(str(x).strip().lower()) for x in local_endswith if isinstance(x, str) and str(x).strip()):
            return False
    local_contains_any = pred.get("local_contains_any")
    if isinstance(local_contains_any, list):
        if not any(str(x).strip().lower() in local for x in local_contains_any if isinstance(x, str) and str(x).strip()):
            return False
    ns_contains = pred.get("ns_contains")
    if isinstance(ns_contains, list):
        if not any(str(x).strip().lower() in ns for x in ns_contains if isinstance(x, str) and str(x).strip()):
            return False
    return True


def _qname_attrs(qname: object) -> tuple[str, str] | None:
    """Lowercased (namespace, localname) for a QName-like object, or None if unreadable."""
    try:
        ns = (qname.getNamespace() or "").lower()  # type: ignore[attr-defined]
        local = (qname.getLocalname() or "").lower()  # type: ignore[attr-defined]
    except Exception:
        return None
    return ns, local


def infer_type_from_qnames(types, rules: dict | None = None) -> str:
    """Classify a WSD host from its service QNames using the rules (precedence-ordered, first wins)."""
    r = rules if isinstance(rules, dict) else cached_wsd_rules()
    default = r.get("default") if isinstance(r.get("default"), str) and r.get("default") else "computer"
    if not types:
        return default

    qname_rules = r.get("qname_rules")
    if not isinstance(qname_rules, list):
        return default

    matched_types: set[str] = set()
    for qname in types:
        attrs = _qname_attrs(qname)
        if attrs is None:
            continue
        ns, local = attrs
        for rule in qname_rules:
            if not isinstance(rule, dict):
                continue
            target = rule.get("type")
            if not isinstance(target, str) or not target.strip() or target in matched_types:
                continue
            preds = rule.get("match_any")
            if not isinstance(preds, list):
                continue
            if any(_predicate_matches(p, ns, local) for p in preds):
                matched_types.add(target)

    precedence = r.get("precedence")
    if isinstance(precedence, list):
        for t in precedence:
            if isinstance(t, str) and t in matched_types:
                return t
    return default
