# File session_autostart.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""XDG autostart desktop entry (~/.config/autostart) for session login."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

_LOG = logging.getLogger(__name__)

_AUTOSTART_FILENAME = "io.esp3d.netneighbor.desktop"


def autostart_file_path() -> Path:
    return Path.home() / ".config" / "autostart" / _AUTOSTART_FILENAME


def autostart_enabled_on_disk() -> bool:
    return autostart_file_path().is_file()


def resolve_exec_command() -> str:
    """Command for Exec= when launching from a saved .desktop file."""
    w = shutil.which("netneighbor")
    if w:
        return w
    root = Path(__file__).resolve().parent.parent
    main_py = root / "main.py"
    if main_py.is_file():
        return f'python3 "{main_py}"'
    return "netneighbor"


def apply_autostart_pref(enabled: bool) -> bool:
    """Create or remove the autostart file. Returns True if state matches *enabled*."""
    path = autostart_file_path()
    if not enabled:
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            _LOG.warning("Could not remove autostart file %s: %s", path, e)
            return not path.exists()
        return True

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _LOG.warning("Could not create autostart directory %s: %s", path.parent, e)
        return False

    exec_cmd = resolve_exec_command()
    # Tray-only startup for login session (menu .desktop stays without this flag — see packaging).
    suffix = "--start-minimized-to-tray"
    if suffix in exec_cmd:
        exec_line = exec_cmd
    else:
        exec_line = f"{exec_cmd} {suffix}"
    body = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=NetNeighbor\n"
        "Comment=LAN discovery (SSDP/mDNS)\n"
        f"Exec={exec_line}\n"
        "Icon=io.esp3d.netneighbor\n"
        "Terminal=false\n"
        "Categories=Network;Utility;\n"
        "X-GNOME-Autostart-enabled=true\n"
        "StartupNotify=false\n"
    )
    try:
        path.write_text(body, encoding="utf-8")
        path.chmod(0o644)
    except OSError as e:
        _LOG.warning("Could not write autostart file %s: %s", path, e)
        return False
    return True
