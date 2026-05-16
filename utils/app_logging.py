# File app_logging.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Shared file + console logging setup (GTK and Qt entrypoints)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_LOG_LEVEL_OFF = 100  # above CRITICAL (50): suppress all standard levels

# Exact stock ``logging.json`` from older releases (no ``app_qt`` key) — useless log file.
_LEGACY_SILENT_STOCK_FILE: dict[str, str] = {
    "default": "NONE",
    "app": "NONE",
    "ui_qt": "NONE",
    "device_list": "NONE",
    "ssdp": "NONE",
    "mdns": "NONE",
    "wsd": "NONE",
    "wsdd": "NONE",
    "nmb": "NONE",
}


def _is_legacy_all_silent_user_config(parsed: dict[str, object]) -> bool:
    """True when the file is only the old all-NONE template (optional ``app_qt`` also NONE)."""
    if not isinstance(parsed, dict) or not parsed:
        return False
    stock_keys = frozenset(_LEGACY_SILENT_STOCK_FILE)
    keys = frozenset(str(k).strip() for k in parsed)
    if keys not in (stock_keys, stock_keys | {"app_qt"}):
        return False
    off = frozenset({"NONE", "OFF", "DISABLED", "SILENT", ""})
    return all(str(v).strip().upper() in off for v in parsed.values())


def _to_level(level_name: str, fallback: int) -> int:
    normalized = str(level_name).strip().upper()
    if normalized in {"NONE", "OFF", "DISABLED", "SILENT"}:
        return _LOG_LEVEL_OFF
    resolved = getattr(logging, normalized, None)
    if isinstance(resolved, int):
        return resolved
    return fallback


def _visible_logging_defaults() -> dict[str, str]:
    """Defaults so ``~/.cache/netneighbor/netneighbor.log`` is useful without tuning."""
    return {
        "default": "INFO",
        "app": "INFO",
        "app_qt": "INFO",
        "ui_qt": "WARNING",
        "device_list": "WARNING",
        "ssdp": "INFO",
        "mdns": "INFO",
        "wsd": "WARNING",
        "wsdd": "WARNING",
        "nmb": "WARNING",
    }


class _FlushingFileHandler(logging.FileHandler):
    """Line-oriented log tailing on Windows/Linux without waiting for buffer fill."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def load_logging_config() -> dict:
    config_dir = Path.home() / ".config" / "netneighbor"
    config_path = config_dir / "logging.json"
    defaults = _visible_logging_defaults()
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        if not config_path.exists():
            config_path.write_text(json.dumps(defaults, indent=2, sort_keys=True), encoding="utf-8")
            return dict(defaults)
        parsed_raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(parsed_raw, dict):
            return dict(defaults)
        parsed = parsed_raw
        if _is_legacy_all_silent_user_config(parsed):
            upgraded = dict(defaults)
            config_path.write_text(json.dumps(upgraded, indent=2, sort_keys=True), encoding="utf-8")
            return upgraded
        merged = dict(defaults)
        for key in defaults:
            if key in parsed and isinstance(parsed[key], str):
                merged[key] = parsed[key].strip()
        return merged
    except (OSError, json.JSONDecodeError):
        return dict(defaults)


def apply_named_log_levels(config: dict) -> None:
    app_level = _to_level(str(config.get("app", config.get("default", "INFO"))), logging.INFO)
    app_qt_level = _to_level(
        str(config.get("app_qt", config.get("app", config.get("default", "INFO")))),
        logging.INFO,
    )
    ssdp_level = _to_level(str(config.get("ssdp", config.get("default", "INFO"))), logging.INFO)
    mdns_level = _to_level(str(config.get("mdns", config.get("default", "INFO"))), logging.INFO)
    wsd_level = _to_level(str(config.get("wsd", config.get("default", "INFO"))), logging.INFO)
    wsdd_level = _to_level(str(config.get("wsdd", config.get("default", "INFO"))), logging.INFO)
    nmb_level = _to_level(str(config.get("nmb", config.get("default", "INFO"))), logging.INFO)
    device_list_level = _to_level(str(config.get("device_list", config.get("default", "INFO"))), logging.INFO)
    ui_qt_level = _to_level(str(config.get("ui_qt", config.get("default", "INFO"))), logging.INFO)

    for logger_name in ("app", "ui", "utils", "discovery.manager", "model"):
        logging.getLogger(logger_name).setLevel(app_level)
    logging.getLogger("app_qt").setLevel(app_qt_level)
    logging.getLogger("ui_qt").setLevel(ui_qt_level)
    logging.getLogger("discovery.ssdp").setLevel(ssdp_level)
    logging.getLogger("discovery.mdns").setLevel(mdns_level)
    logging.getLogger("discovery.wsd").setLevel(wsd_level)
    logging.getLogger("discovery.wsdd_client").setLevel(wsdd_level)
    logging.getLogger("discovery.netbios").setLevel(nmb_level)
    logging.getLogger("discovery.manager.ssdp").setLevel(ssdp_level)
    logging.getLogger("discovery.manager.mdns").setLevel(mdns_level)
    logging.getLogger("discovery.manager.wsd").setLevel(wsd_level)
    logging.getLogger("discovery.manager.wsdd").setLevel(wsdd_level)
    logging.getLogger("discovery.manager.nmb").setLevel(nmb_level)
    logging.getLogger("ui.device_list").setLevel(device_list_level)


def _apply_netneighbor_debug_ui_env(*, log_name: str) -> None:
    """Raise loggers used for Qt / discovery UI troubleshooting when env is set."""
    dbg_ui = str(os.getenv("NETNEIGHBOR_DEBUG_UI", "")).strip().lower()
    if dbg_ui not in ("1", "true", "yes", "on", "debug"):
        return
    for name in ("ui_qt", "discovery.netbios"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    logging.getLogger(log_name).info(
        "NETNEIGHBOR_DEBUG_UI: DEBUG for loggers ui_qt, discovery.netbios"
    )


def setup_logging(*, log_name: str = "app") -> None:
    """Configure root logging once; respects ``NETNEIGHBOR_LOG_LEVEL`` and ``logging.json``."""
    env_level_name = os.getenv("NETNEIGHBOR_LOG_LEVEL")
    config = load_logging_config()
    default_level_name = str(config.get("default", "INFO")).upper()
    if env_level_name:
        default_level_name = env_level_name.upper()
    level = _to_level(default_level_name, logging.INFO)
    log_dir = Path.home() / ".cache" / "netneighbor"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "netneighbor.log"

    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.setLevel(level)
        apply_named_log_levels(config)
        _apply_netneighbor_debug_ui_env(log_name=log_name)
        return

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    file_handler = _FlushingFileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)
    apply_named_log_levels(config)
    _apply_netneighbor_debug_ui_env(log_name=log_name)
    logging.getLogger(log_name).info("Logging initialized at %s (%s)", default_level_name, log_file)
