# File custom_command.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""User-defined external command per device (placeholders: {ip}, {port}, {name}, {type}, {category}, {url})."""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
import threading
from collections.abc import Callable

from utils.scheduling import ScheduleMainFn

_LOG = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
# {ip_raw} is kept as a silent alias for {ip} for backwards compatibility
# with any saved preferences — it is no longer shown in the UI.
_KNOWN = frozenset({"ip", "ip_raw", "port", "name", "type", "category", "url"})


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
    Substitute placeholders then split the result into argv.

    Values are shell-quoted before substitution so that ``shlex.split`` can
    correctly reassemble tokens containing spaces (e.g. device names).
    ``{ip_raw}`` is a silent alias for ``{ip}`` kept for backwards compat.
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
        "ip_raw": ip,
        "port": str(int(port)),
        "name": name,
        "type": type_,
        "category": category,
        "url": url,
    }
    substituted = raw
    # ip_raw is substituted raw (no shlex quoting) — keep for UNC path compat.
    substituted = substituted.replace("{ip_raw}", values["ip_raw"])
    for key in ("ip", "port", "name", "type", "category", "url"):
        substituted = substituted.replace("{" + key + "}", shlex.quote(str(values[key])))

    if "{" in substituted:
        _LOG.warning("custom command: unreplaced placeholders remain")
        return None

    try:
        argv = shlex.split(substituted, posix=True)
    except ValueError as e:
        _LOG.warning("custom command: shlex failed: %s", e)
        return None
    return argv or None


def spawn_custom_command_detached(
    argv: list[str],
    *,
    on_error: Callable[[str], None],
    schedule_on_main: ScheduleMainFn | None = None,
) -> None:
    """Run *argv* in a background thread; marshal ``on_error`` to the UI thread when *schedule_on_main* is set."""

    def _run() -> None:
        _LOG.debug("spawn: %s", argv)
        try:
            subprocess.Popen(argv, close_fds=True, start_new_session=True)
        except OSError as e:
            _LOG.warning("custom command spawn failed: %s", e)

            def _fail() -> None:
                on_error(str(e))

            if schedule_on_main is not None:
                schedule_on_main(_fail)
            else:
                _fail()

    threading.Thread(target=_run, daemon=True).start()
