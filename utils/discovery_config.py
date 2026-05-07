# File discovery_config.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Load discovery protocol toggles from user config."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Sequence

_DEFAULT_STARTUP_REFRESH = [15, 30, 60, 120, 300, 600]

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
# WSD: WS-Discovery probe interval and per-probe wait (discovery/wsd.py); requires PyPI ``WSDiscovery``.
_DEFAULT_WSD_QUERY = {
    "interval_seconds": 90,
    "timeout_seconds": 8.0,
}
# NetBIOS: periodic ``nmblookup`` (discovery/netbios.py); requires Samba ``nmblookup`` on PATH.
_DEFAULT_NMB_QUERY = {
    "interval_seconds": 60,
    "timeout_seconds": 10,
    # ``-S`` requests node status so hostnames are real (plain ``*`` only yields ``*<00>`` per IP).
    "argv": ["-S", "*"],
    # Optional explicit IPs for ``nmblookup -A`` when broadcast browse misses PCs (same subnet).
    "directed_ips": [],
}
# wsdd: poll local `wsdd` control socket (``-l``) in discovery mode (``-D``); see ``discovery/wsdd_client.py``.
_DEFAULT_WSDD_QUERY = {
    "listen": "",
    "interval_seconds": 60.0,
    "socket_timeout_seconds": 4.0,
    "probe_each_poll": True,
}

# Cross-protocol ordering for the same host — canonical ladder is ``merge.information_precedence``
# in discovery.json (verified keys only; must list all roles exactly once to customize order).
#
# Semantics: **first role in the list = strongest** when picking which protocol row drives name/type for a
# bundled host; **last role = weakest** among merge roles. This only affects merge/display, not how fast or
# whether a probe finds a device (discovery “weight” is separate: timeouts, firewalls, ``protocol_order`` ties).
#
# Roles: user_override (prefs), ssdp_live (UPnP), wsdd_live (local wsdd daemon cache), wsd_live (WS-Discovery),
# nmb_live (NetBIOS/nmblookup), ssdp_profile_cache (disk on mDNS), mdns (service inference).
# ``protocol_order``: live protocol ``source`` ids (``ssdp``, ``wsd``, ``nmb``, ``mdns``, …) for UI tie-breaks.
_ALLOWED_MERGE_INFORMATION_ROLES = frozenset(
    {"user_override", "ssdp_live", "wsdd_live", "wsd_live", "nmb_live", "ssdp_profile_cache", "mdns"}
)
_LEGACY_INFORMATION_ROLES = frozenset({"user_override", "ssdp_live", "ssdp_profile_cache", "mdns"})
_LEGACY_WITH_WSD_ONLY = frozenset(
    {"user_override", "ssdp_live", "wsd_live", "ssdp_profile_cache", "mdns"}
)
# Saved configs from before ``wsdd_live`` existed (six roles, no ``wsdd_live``).
_LEGACY_MERGE_ROLES_WITHOUT_WSDD_CLIENT = frozenset(
    {"user_override", "ssdp_live", "wsd_live", "nmb_live", "ssdp_profile_cache", "mdns"}
)
_DEFAULT_MERGE = {
    "protocol_order": ["ssdp", "wsd", "wsdd", "nmb", "mdns"],
    # Strongest → weakest for merged list/detail primary row (same machine, several protocols).
    "information_precedence": [
        "user_override",
        "ssdp_live",
        "ssdp_profile_cache",
        "mdns",
        "nmb_live",
        "wsdd_live",
        "wsd_live",  # weakest merge role by default: generic WSD label loses to SSDP/mDNS/nmb/wsdd above
    ],
    "show_ip_in_device_list": True,
}


def _ensure_nmb_before_wsd(roles: list[str]) -> list[str]:
    """Prefer NetBIOS / ``nmblookup`` names (neighborhood) over generic WSD labels for the same host."""
    if "nmb_live" not in roles or "wsd_live" not in roles:
        return list(roles)
    out = list(roles)
    i_wsd = out.index("wsd_live")
    i_nmb = out.index("nmb_live")
    if i_nmb < i_wsd:
        return out
    out.remove("nmb_live")
    out.insert(i_wsd, "nmb_live")
    return out


def normalize_information_precedence_list(src: object) -> list[str]:
    """Return a validated ``information_precedence`` list or defaults.

    Custom order must list each merge role exactly once; otherwise defaults apply.
    Older lists without ``wsd_live`` / ``nmb_live`` are migrated by inserting those roles after
    ``ssdp_live`` / ``wsd_live``.
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
        return _ensure_nmb_before_wsd(cleaned)

    if seen == _LEGACY_MERGE_ROLES_WITHOUT_WSDD_CLIENT:
        migrated = list(cleaned)
        if "wsd_live" in migrated:
            migrated.insert(migrated.index("wsd_live"), "wsdd_live")
        else:
            migrated.append("wsdd_live")
        return _ensure_nmb_before_wsd(migrated)

    migrated = list(cleaned)
    cur = seen
    if cur == _LEGACY_INFORMATION_ROLES:
        tmp: list[str] = []
        for role in migrated:
            tmp.append(role)
            if role == "ssdp_live":
                tmp.append("wsd_live")
        migrated = tmp
        cur = frozenset(migrated)
    if cur == _LEGACY_WITH_WSD_ONLY:
        tmp = []
        for role in migrated:
            tmp.append(role)
            if role == "wsd_live":
                tmp.append("nmb_live")
        migrated = tmp
        cur = frozenset(migrated)
    if cur == _ALLOWED_MERGE_INFORMATION_ROLES:
        return _ensure_nmb_before_wsd(migrated)
    return default


def information_precedence_rank(order: Sequence[str], role: str) -> int:
    """Return index of ``role`` in ``order``. Lower value = stronger merge precedence; last roles are weakest."""
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
    if s == "wsd":
        return "wsd_live"
    if s == "wsdd":
        return "wsdd_live"
    if s == "nmb":
        return "nmb_live"
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
        "wsd": {
            "enabled": True,
            "query": dict(_DEFAULT_WSD_QUERY),
        },
        "nmb": {
            "enabled": True,
            "query": dict(_DEFAULT_NMB_QUERY),
        },
        "wsdd": {
            "enabled": False,
            "query": dict(_DEFAULT_WSDD_QUERY),
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
    elif protocol == "wsd":
        if "interval_seconds" in pq:
            base_q["interval_seconds"] = _clamp_float(
                pq.get("interval_seconds"),
                float(_DEFAULT_WSD_QUERY["interval_seconds"]),
                15.0,
                3600.0,
            )
        if "timeout_seconds" in pq:
            base_q["timeout_seconds"] = _clamp_float(
                pq.get("timeout_seconds"),
                float(_DEFAULT_WSD_QUERY["timeout_seconds"]),
                2.0,
                60.0,
            )
    elif protocol == "wsdd":
        if "interval_seconds" in pq:
            base_q["interval_seconds"] = _clamp_float(
                pq.get("interval_seconds"),
                float(_DEFAULT_WSDD_QUERY["interval_seconds"]),
                15.0,
                3600.0,
            )
        if "socket_timeout_seconds" in pq:
            base_q["socket_timeout_seconds"] = _clamp_float(
                pq.get("socket_timeout_seconds"),
                float(_DEFAULT_WSDD_QUERY["socket_timeout_seconds"]),
                1.0,
                60.0,
            )
        if "listen" in pq and isinstance(pq.get("listen"), str):
            base_q["listen"] = pq.get("listen", "").strip()
        if "probe_each_poll" in pq:
            base_q["probe_each_poll"] = _coerce_bool(pq.get("probe_each_poll"), bool(_DEFAULT_WSDD_QUERY["probe_each_poll"]))
    elif protocol == "nmb":
        if "interval_seconds" in pq:
            base_q["interval_seconds"] = _clamp_float(
                pq.get("interval_seconds"),
                float(_DEFAULT_NMB_QUERY["interval_seconds"]),
                60.0,
                3600.0,
            )
        if "timeout_seconds" in pq:
            base_q["timeout_seconds"] = _clamp_float(
                pq.get("timeout_seconds"),
                float(_DEFAULT_NMB_QUERY["timeout_seconds"]),
                5.0,
                120.0,
            )
        if "argv" in pq and isinstance(pq.get("argv"), list):
            av = [str(x) for x in pq["argv"] if isinstance(x, str) and str(x).strip()]
            if av:
                base_q["argv"] = av
        if "directed_ips" in pq and isinstance(pq.get("directed_ips"), list):
            ips: list[str] = []
            for x in pq["directed_ips"]:
                if isinstance(x, str) and x.strip():
                    ips.append(x.strip())
            base_q["directed_ips"] = ips[:64]


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


def _normalize_wsd_block(src: object) -> dict[str, Any]:
    out = copy.deepcopy(_default_nested()["wsd"])
    if not isinstance(src, dict):
        return out
    if "enabled" in src:
        out["enabled"] = _coerce_bool(src.get("enabled"), out["enabled"])
    _merge_query_into(out, src, "wsd")
    return out


def _normalize_nmb_block(src: object) -> dict[str, Any]:
    out = copy.deepcopy(_default_nested()["nmb"])
    if not isinstance(src, dict):
        return out
    if "enabled" in src:
        out["enabled"] = _coerce_bool(src.get("enabled"), out["enabled"])
    _merge_query_into(out, src, "nmb")
    q = out.get("query") if isinstance(out.get("query"), dict) else {}
    av = q.get("argv")
    if isinstance(av, list) and len(av) == 1 and str(av[0]).strip() == "*":
        q["argv"] = ["-S", "*"]
    return out


def _normalize_wsdd_block(src: object) -> dict[str, Any]:
    out = copy.deepcopy(_default_nested()["wsdd"])
    if not isinstance(src, dict):
        return out
    if "enabled" in src:
        out["enabled"] = _coerce_bool(src.get("enabled"), out["enabled"])
    _merge_query_into(out, src, "wsdd")
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
    if "show_ip_in_device_list" in src:
        out["show_ip_in_device_list"] = _coerce_bool(src.get("show_ip_in_device_list"), bool(out.get("show_ip_in_device_list", True)))
    return out


def _disk_repr(cfg: dict[str, Any]) -> dict[str, Any]:
    """Human-friendly JSON: booleans as true/false, startup as comma-separated string at root."""
    mdns = cfg.get("mdns") if isinstance(cfg.get("mdns"), dict) else {}
    ssdp = cfg.get("ssdp") if isinstance(cfg.get("ssdp"), dict) else {}
    wsd = cfg.get("wsd") if isinstance(cfg.get("wsd"), dict) else {}
    nmb = cfg.get("nmb") if isinstance(cfg.get("nmb"), dict) else {}
    wsdd = cfg.get("wsdd") if isinstance(cfg.get("wsdd"), dict) else {}
    mq = mdns.get("query") if isinstance(mdns.get("query"), dict) else {}
    sq = ssdp.get("query") if isinstance(ssdp.get("query"), dict) else {}
    wq = wsd.get("query") if isinstance(wsd.get("query"), dict) else {}
    nq = nmb.get("query") if isinstance(nmb.get("query"), dict) else {}
    wx = wsdd.get("query") if isinstance(wsdd.get("query"), dict) else {}
    merge = cfg.get("merge") if isinstance(cfg.get("merge"), dict) else {}
    po = merge.get("protocol_order") if isinstance(merge.get("protocol_order"), list) else _DEFAULT_MERGE["protocol_order"]
    protocol_order_out = [str(x).strip().lower() for x in po if isinstance(x, str) and str(x).strip()]
    ip_out = normalize_information_precedence_list(merge.get("information_precedence"))
    show_ip = bool(merge.get("show_ip_in_device_list", True))
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
        "wsd": {
            "enabled": bool(wsd.get("enabled", True)),
            "query": {
                "interval_seconds": float(wq.get("interval_seconds", _DEFAULT_WSD_QUERY["interval_seconds"])),
                "timeout_seconds": float(wq.get("timeout_seconds", _DEFAULT_WSD_QUERY["timeout_seconds"])),
            },
        },
        "nmb": {
            "enabled": bool(nmb.get("enabled", True)),
            "query": {
                "interval_seconds": float(
                    nq.get("interval_seconds", _DEFAULT_NMB_QUERY["interval_seconds"])
                ),
                "timeout_seconds": float(
                    nq.get("timeout_seconds", _DEFAULT_NMB_QUERY["timeout_seconds"])
                ),
                **(
                    {"argv": list(nq["argv"])}
                    if isinstance(nq.get("argv"), list) and nq.get("argv")
                    else {}
                ),
                **(
                    {"directed_ips": [str(x).strip() for x in nq["directed_ips"] if str(x).strip()]}
                    if isinstance(nq.get("directed_ips"), list) and nq.get("directed_ips")
                    else {}
                ),
            },
        },
        "wsdd": {
            "enabled": bool(wsdd.get("enabled", False)),
            "query": {
                "listen": str(wx.get("listen", _DEFAULT_WSDD_QUERY["listen"]) or "").strip(),
                "interval_seconds": float(wx.get("interval_seconds", _DEFAULT_WSDD_QUERY["interval_seconds"])),
                "socket_timeout_seconds": float(
                    wx.get("socket_timeout_seconds", _DEFAULT_WSDD_QUERY["socket_timeout_seconds"])
                ),
                "probe_each_poll": bool(wx.get("probe_each_poll", _DEFAULT_WSDD_QUERY["probe_each_poll"])),
            },
        },
        "merge": {
            "protocol_order": protocol_order_out or list(_DEFAULT_MERGE["protocol_order"]),
            "information_precedence": ip_out,
            "show_ip_in_device_list": show_ip,
        },
        "startup_refresh_seconds": startup_str,
    }
    return out


def load_discovery_protocol_config() -> dict[str, object]:
    """Return discovery settings from ~/.config/netneighbor/discovery.json.

    Expected shape: top-level ``mdns`` / ``ssdp`` / ``wsd`` objects (``wsd`` has ``enabled`` and optional
    ``query``: ``interval_seconds``, ``timeout_seconds``), optional ``merge.protocol_order``
    (ordered ``source`` ids: ``ssdp``, ``wsd``, ``mdns``, …), optional ``merge.information_precedence``
    (roles ``user_override``, ``ssdp_live``, ``wsd_live``, ``ssdp_profile_cache``, ``mdns``), and root
    ``startup_refresh_seconds``. Malformed or missing file
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
            "wsd": _normalize_wsd_block(parsed.get("wsd")),
            "nmb": _normalize_nmb_block(parsed.get("nmb")),
            "wsdd": _normalize_wsdd_block(parsed.get("wsdd")),
            "merge": _normalize_merge(parsed.get("merge")),
            "startup_refresh_seconds": _normalize_startup_refresh(parsed.get("startup_refresh_seconds")),
        }
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(defaults)
