"""Load discovery protocol toggles from user config."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Sequence

_DEFAULT_STARTUP_REFRESH = [20, 45, 90]

# mDNS: enumeration and per-service resolution (see discovery/mdns.py).
_DEFAULT_MDNS_QUERY = {
    "enumeration_timeout_seconds": 2.5,
    "enumeration_interval_seconds": 240,
    "service_info_timeout_ms": 2500,
}
# SSDP: periodic M-SEARCH cadence and MX (max wait, seconds) in each M-SEARCH (discovery/ssdp.py).
_DEFAULT_SSDP_QUERY = {
    "interval_seconds": 60,
    "mx_seconds": 5,
    # Minimum seconds between HTTP GETs for descriptor URLs with the same host IP (or same hostname);
    # avoids duplicate fetches when anticipatory + SSDP LOCATION arrive close together. 0 = off.
    "descriptor_http_min_interval_seconds": 5.0,
}

# Cross-protocol ordering for the same host — canonical ladder is ``merge.information_precedence``
# in discovery.json (verified keys only; must list all roles exactly once to customize order).
# Roles: user_override (prefs), ssdp_live (UPnP responses), ssdp_profile_cache (disk hints on mDNS rows),
# mdns (service inference).
# ``protocol_order``: subset used when resolving ties between live protocol rows by ``Device.source``
# (see ``information_precedence_role_for_device_source``).
_ALLOWED_MERGE_INFORMATION_ROLES = frozenset(
    {"user_override", "ssdp_live", "ssdp_profile_cache", "mdns"}
)
_DEFAULT_MERGE = {
    "protocol_order": ["ssdp", "mdns"],
    "information_precedence": [
        "user_override",
        "ssdp_live",
        "ssdp_profile_cache",
        "mdns",
    ],
}


def normalize_information_precedence_list(src: object) -> list[str]:
    """Return a validated ``information_precedence`` list or defaults.

    Custom order must list each merge role exactly once; otherwise defaults apply.
    """
    default = list(_DEFAULT_MERGE["information_precedence"])
    if not isinstance(src, list):
        return default
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in src:
        if not isinstance(item, str):
            continue
        s = item.strip().lower()
        if s in _ALLOWED_MERGE_INFORMATION_ROLES and s not in seen:
            cleaned.append(s)
            seen.add(s)
    if seen == _ALLOWED_MERGE_INFORMATION_ROLES:
        return cleaned
    return default


def information_precedence_rank(order: Sequence[str], role: str) -> int:
    """Lower index = stronger precedence."""
    r = (role or "").strip().lower()
    for i, x in enumerate(order):
        if str(x).strip().lower() == r:
            return i
    return len(order)


def information_precedence_role_for_device_source(source: str) -> str:
    """Map :attr:`~model.device.Device.source` to a merge ``information_precedence`` role."""
    s = (source or "").strip().lower()
    if s == "ssdp":
        return "ssdp_live"
    if s == "mdns":
        return "mdns"
    return s


def _default_nested() -> dict[str, Any]:
    return {
        "mdns": {
            "enabled": True,
            "rules": True,
            "query": dict(_DEFAULT_MDNS_QUERY),
        },
        "ssdp": {
            "enabled": True,
            "rules": True,
            "query": dict(_DEFAULT_SSDP_QUERY),
        },
        "merge": dict(_DEFAULT_MERGE),
        "startup_refresh_seconds": list(_DEFAULT_STARTUP_REFRESH),
    }


def _coerce_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) != 0
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off", ""):
            return False
    return default


def _parse_startup_refresh(value: object) -> list[int] | None:
    if value is None:
        return None
    if isinstance(value, list):
        cleaned: list[int] = []
        for item in value:
            try:
                delay = int(item)
            except (TypeError, ValueError):
                continue
            if 1 <= delay <= 300 and delay not in cleaned:
                cleaned.append(delay)
        return cleaned or None
    if isinstance(value, str):
        parts = value.replace(",", " ").split()
        cleaned = []
        for part in parts:
            try:
                delay = int(part)
            except ValueError:
                continue
            if 1 <= delay <= 300 and delay not in cleaned:
                cleaned.append(delay)
        return cleaned or None
    try:
        delay = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if 1 <= delay <= 300:
        return [delay]
    return None


def _clamp_int(value: object, default: int, low: int, high: int) -> int:
    try:
        v = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(high, v))


def _clamp_float(value: object, default: float, low: float, high: float) -> float:
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(high, v))


def _merge_query_into(out_proto: dict[str, Any], parsed_block: object, protocol: str) -> None:
    if not isinstance(parsed_block, dict):
        return
    pq = parsed_block.get("query")
    if not isinstance(pq, dict):
        return
    base_q = out_proto.setdefault("query", {})
    if protocol == "mdns":
        if "enumeration_timeout_seconds" in pq:
            base_q["enumeration_timeout_seconds"] = _clamp_float(
                pq.get("enumeration_timeout_seconds"),
                float(_DEFAULT_MDNS_QUERY["enumeration_timeout_seconds"]),
                2.0,
                3.0,
            )
        if "enumeration_interval_seconds" in pq:
            base_q["enumeration_interval_seconds"] = _clamp_int(
                pq.get("enumeration_interval_seconds"),
                int(_DEFAULT_MDNS_QUERY["enumeration_interval_seconds"]),
                30,
                86400,
            )
        if "service_info_timeout_ms" in pq:
            base_q["service_info_timeout_ms"] = _clamp_int(
                pq.get("service_info_timeout_ms"),
                int(_DEFAULT_MDNS_QUERY["service_info_timeout_ms"]),
                2000,
                3000,
            )
    elif protocol == "ssdp":
        if "interval_seconds" in pq:
            base_q["interval_seconds"] = _clamp_int(
                pq.get("interval_seconds"),
                int(_DEFAULT_SSDP_QUERY["interval_seconds"]),
                10,
                3600,
            )
        if "mx_seconds" in pq:
            base_q["mx_seconds"] = _clamp_int(
                pq.get("mx_seconds"),
                int(_DEFAULT_SSDP_QUERY["mx_seconds"]),
                5,
                6,
            )
        if "descriptor_http_min_interval_seconds" in pq:
            v = _clamp_float(
                pq.get("descriptor_http_min_interval_seconds"),
                float(_DEFAULT_SSDP_QUERY["descriptor_http_min_interval_seconds"]),
                0.0,
                120.0,
            )
            base_q["descriptor_http_min_interval_seconds"] = v


def _normalize_mdns_block(src: object) -> dict[str, Any]:
    out = copy.deepcopy(_default_nested()["mdns"])
    if not isinstance(src, dict):
        return out
    if "enabled" in src:
        out["enabled"] = _coerce_bool(src.get("enabled"), out["enabled"])
    if "rules" in src:
        out["rules"] = _coerce_bool(src.get("rules"), out["rules"])
    _merge_query_into(out, src, "mdns")
    return out


def _normalize_ssdp_block(src: object) -> dict[str, Any]:
    out = copy.deepcopy(_default_nested()["ssdp"])
    if not isinstance(src, dict):
        return out
    if "enabled" in src:
        out["enabled"] = _coerce_bool(src.get("enabled"), out["enabled"])
    if "rules" in src:
        out["rules"] = _coerce_bool(src.get("rules"), out["rules"])
    _merge_query_into(out, src, "ssdp")
    return out


def _normalize_startup_refresh(value: object) -> list[int]:
    cleaned = _parse_startup_refresh(value)
    if cleaned:
        return cleaned
    return list(_DEFAULT_STARTUP_REFRESH)


def _normalize_merge(src: object) -> dict[str, Any]:
    out = copy.deepcopy(_DEFAULT_MERGE)
    if not isinstance(src, dict):
        return out
    po = src.get("protocol_order")
    if isinstance(po, list):
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in po:
            if not isinstance(item, str):
                continue
            s = item.strip().lower()
            if not s or s in seen:
                continue
            seen.add(s)
            cleaned.append(s)
        if cleaned:
            out["protocol_order"] = cleaned
    out["information_precedence"] = normalize_information_precedence_list(src.get("information_precedence"))
    return out


def _disk_repr(cfg: dict[str, Any]) -> dict[str, Any]:
    """Human-friendly JSON: booleans as true/false, startup as comma-separated string at root."""
    mdns = cfg.get("mdns") if isinstance(cfg.get("mdns"), dict) else {}
    ssdp = cfg.get("ssdp") if isinstance(cfg.get("ssdp"), dict) else {}
    mq = mdns.get("query") if isinstance(mdns.get("query"), dict) else {}
    sq = ssdp.get("query") if isinstance(ssdp.get("query"), dict) else {}
    merge = cfg.get("merge") if isinstance(cfg.get("merge"), dict) else {}
    po = merge.get("protocol_order") if isinstance(merge.get("protocol_order"), list) else _DEFAULT_MERGE["protocol_order"]
    protocol_order_out = [str(x).strip().lower() for x in po if isinstance(x, str) and str(x).strip()]
    ip_out = normalize_information_precedence_list(merge.get("information_precedence"))
    delays = cfg.get("startup_refresh_seconds")
    if not isinstance(delays, list):
        delays = list(_DEFAULT_STARTUP_REFRESH)
    startup_str = ", ".join(str(int(x)) for x in delays)
    out: dict[str, Any] = {
        "mdns": {
            "enabled": bool(mdns.get("enabled", True)),
            "rules": bool(mdns.get("rules", True)),
            "query": {
                "enumeration_timeout_seconds": float(
                    mq.get("enumeration_timeout_seconds", _DEFAULT_MDNS_QUERY["enumeration_timeout_seconds"])
                ),
                "enumeration_interval_seconds": int(
                    mq.get("enumeration_interval_seconds", _DEFAULT_MDNS_QUERY["enumeration_interval_seconds"])
                ),
                "service_info_timeout_ms": int(
                    mq.get("service_info_timeout_ms", _DEFAULT_MDNS_QUERY["service_info_timeout_ms"])
                ),
            },
        },
        "ssdp": {
            "enabled": bool(ssdp.get("enabled", True)),
            "rules": bool(ssdp.get("rules", True)),
            "query": {
                "interval_seconds": int(sq.get("interval_seconds", _DEFAULT_SSDP_QUERY["interval_seconds"])),
                "mx_seconds": int(sq.get("mx_seconds", _DEFAULT_SSDP_QUERY["mx_seconds"])),
                "descriptor_http_min_interval_seconds": float(
                    sq.get(
                        "descriptor_http_min_interval_seconds",
                        _DEFAULT_SSDP_QUERY["descriptor_http_min_interval_seconds"],
                    )
                ),
            },
        },
        "merge": {
            "protocol_order": protocol_order_out or list(_DEFAULT_MERGE["protocol_order"]),
            "information_precedence": ip_out,
        },
        "startup_refresh_seconds": startup_str,
    }
    return out


def load_discovery_protocol_config() -> dict[str, object]:
    """Return discovery settings from ~/.config/netneighbor/discovery.json.

    Expected shape: top-level ``mdns`` / ``ssdp`` objects (each with ``enabled``, ``rules``,
    optional ``query``), optional ``merge.protocol_order`` (ordered ``source`` ids for live rows),
    optional ``merge.information_precedence`` (full ordering of ``user_override``, ``ssdp_live``,
    ``ssdp_profile_cache``, ``mdns``), and root ``startup_refresh_seconds``. Malformed or missing file
    yields defaults; defaults are written only when the file does not exist.

    The default ``merge.information_precedence`` places ``user_override`` first (strongest); the manager
    applies user type/name/location prefs after composing discovery rows so they override protocol hints.
    """
    cfg_dir = Path.home() / ".config" / "netneighbor"
    cfg_path = cfg_dir / "discovery.json"
    defaults = _default_nested()
    try:
        cfg_dir.mkdir(parents=True, exist_ok=True)
        if not cfg_path.exists():
            cfg_path.write_text(json.dumps(_disk_repr(defaults), indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return copy.deepcopy(defaults)

        parsed_raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        if not isinstance(parsed_raw, dict):
            return copy.deepcopy(defaults)

        parsed: dict[str, Any] = parsed_raw
        return {
            "mdns": _normalize_mdns_block(parsed.get("mdns")),
            "ssdp": _normalize_ssdp_block(parsed.get("ssdp")),
            "merge": _normalize_merge(parsed.get("merge")),
            "startup_refresh_seconds": _normalize_startup_refresh(parsed.get("startup_refresh_seconds")),
        }
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(defaults)
