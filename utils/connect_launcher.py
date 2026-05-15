# File connect_launcher.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Launch resolved connect URIs: system default (empty template) or per-scheme command from preferences."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from utils.browser import open_url
from utils.custom_command import build_argv_from_template, spawn_custom_command_detached

_LOG = logging.getLogger(__name__)
_SCHEME_ORDER = ("http", "https", "smb", "ftp", "ssh", "telnet", "sftp")

_SKIP_JSON_KEYS = frozenset({"version", "comment", "schema"})

_HARDCODED_DEFAULTS: dict[str, str] = {
    "http": "xdg-open http://{ip}",
    "https": "xdg-open https://{ip}",
    "smb": "nemo smb://{ip}",
    "ftp": "nemo ftp://{ip}",
    "ssh": "x-terminal-emulator -e ssh -p {port} {ip}",
    "telnet": "x-terminal-emulator -e nc {ip} {port}",
    "sftp": "nemo sftp://{ip}",
}


def _platform_branch_key() -> str:
    p = sys.platform
    if p == "win32":
        return "win32"
    if p == "darwin":
        return "darwin"
    return "linux"


def _coerce_scheme_map(obj: object) -> dict[str, str]:
    if not isinstance(obj, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in obj.items():
        if not isinstance(k, str) or k in _SKIP_JSON_KEYS:
            continue
        if isinstance(v, str) and v.strip():
            out[k] = v
    return out


def _json_has_os_branches(data: dict) -> bool:
    for k in ("linux", "darwin", "win32"):
        v = data.get(k)
        if isinstance(v, dict) and v:
            return True
    return False


def _merge_templates_from_json_doc(data: dict) -> dict[str, str]:
    """Extract scheme→template from either per-OS branches or legacy flat mapping."""
    if _json_has_os_branches(data):
        merged: dict[str, str] = {}
        linux_b = data.get("linux")
        if isinstance(linux_b, dict):
            merged.update(_coerce_scheme_map(linux_b))
        plat = data.get(_platform_branch_key())
        if isinstance(plat, dict):
            merged.update(_coerce_scheme_map(plat))
        return merged
    return _coerce_scheme_map(data)


def _load_json_defaults() -> dict[str, str]:
    json_path = Path(__file__).resolve().parent.parent / "config" / "default_commands.json"
    try:
        with json_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        _LOG.debug("Could not load %s; using hardcoded defaults", json_path)
        return {}
    if not isinstance(data, dict):
        return {}
    return _merge_templates_from_json_doc(data)


def default_connect_command_templates() -> dict[str, str]:
    defaults = dict(_HARDCODED_DEFAULTS)
    defaults.update(_load_json_defaults())
    return defaults


def normalize_connect_templates(raw: object) -> dict[str, str]:
    out = default_connect_command_templates()
    if isinstance(raw, dict):
        for k in out:
            v = raw.get(k)
            if isinstance(v, str):
                out[k] = v
    return out


def launch_connect_for_uri(
    uri: str,
    templates: dict[str, str],
    *,
    ip: str,
    port: int,
    name: str,
    type_: str,
    category: str,
    device_cmd_override: str = "",
    on_spawn_error: Callable[[str], None] | None = None,
) -> str | None:
    """
    Open *uri* with the desktop default, or run the template for ``urlparse(uri).scheme``.

    If *device_cmd_override* is set it takes priority over the scheme template from *templates*.
    ``{ip}`` and ``{port}`` in the template are resolved from the URI itself when present
    (this transparently respects per-device endpoint overrides).

    Returns a **user-visible error** when the template is set but invalid; ``None`` on success
    (including spawn scheduled). Spawn failures invoke *on_spawn_error* asynchronously.
    """
    u = (uri or "").strip()
    if not u:
        return None

    parsed = urlparse(u)
    scheme = parsed.scheme.lower()
    if not scheme:
        open_url(u)
        return None

    # Prefer ip/port embedded in the URI (handles endpoint overrides automatically).
    effective_ip = parsed.hostname or ip
    effective_port = parsed.port if parsed.port is not None else port

    # Per-device command override > scheme template > open_url fallback.
    tmpl = (device_cmd_override or "").strip()
    if not tmpl:
        tmpl = ((templates.get(scheme) if isinstance(templates, dict) else None) or "").strip()
    if not tmpl:
        open_url(u)
        return None

    argv = build_argv_from_template(
        tmpl,
        ip=effective_ip,
        port=effective_port,
        name=name,
        type_=type_,
        category=category,
        url=u,
    )
    if argv is None:
        return _("Invalid command template for this protocol.")

    spawn_custom_command_detached(argv, on_error=on_spawn_error or (lambda _s: None))
    return None
