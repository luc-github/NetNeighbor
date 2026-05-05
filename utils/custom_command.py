"""User-defined external command per device (placeholders: {ip}, {port}, {name}, {type}, {category}, {url})."""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
import threading
from collections.abc import Callable

_LOG = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
_KNOWN = frozenset({"ip", "port", "name", "type", "category", "url"})


def build_argv_from_template(
    template: str,
    *,
    ip: str,
    port: int,
    name: str,
    type_: str,
    category: str,
    url: str = "",
) -> list[str] | None:
    """
    Substitute placeholders with **shell-quoted** values, then ``shlex.split`` the result.

    Returns ``None`` if *template* is empty/whitespace or parsing fails (unknown placeholder, empty argv).
    """
    raw = (template or "").strip()
    if not raw:
        return None

    for m in _PLACEHOLDER_RE.finditer(raw):
        key = m.group(1)
        if key not in _KNOWN:
            _LOG.warning("custom command: unknown placeholder {%s}", key)
            return None

    values = {
        "ip": ip,
        "port": str(int(port)),
        "name": name,
        "type": type_,
        "category": category,
        "url": url,
    }
    substituted = raw
    for key in _KNOWN:
        if key not in values:
            continue
        substituted = substituted.replace("{" + key + "}", shlex.quote(str(values[key])))

    if "{" in substituted:
        _LOG.warning("custom command: unreplaced placeholders remain")
        return None

    try:
        argv = shlex.split(substituted, posix=True)
    except ValueError as e:
        _LOG.warning("custom command: shlex failed: %s", e)
        return None
    if not argv:
        return None
    return argv


def spawn_custom_command_detached(argv: list[str], *, on_error: Callable[[str], None]) -> None:
    """Run *argv* in a background thread; call ``on_error(str)`` from the GTK idle thread on failure."""

    def _run() -> None:
        try:
            subprocess.Popen(argv, close_fds=True, start_new_session=True)
        except OSError as e:
            _LOG.warning("custom command spawn failed: %s", e)

            def _fail() -> None:
                on_error(str(e))

            try:
                from gi.repository import GLib

                GLib.idle_add(_fail)
            except Exception:
                on_error(str(e))

    threading.Thread(target=_run, daemon=True).start()
