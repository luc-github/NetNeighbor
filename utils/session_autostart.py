# File session_autostart.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Session-login autostart: XDG .desktop (Linux/macOS) + registry Run key (Windows)."""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

_LOG = logging.getLogger(__name__)

_AUTOSTART_FILENAME = "io.esp3d.netneighbor.desktop"
_WIN_REG_VALUE = "NetNeighbor"
_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# ── helpers ────────────────────────────────────────────────────────────────────

def _is_windows() -> bool:
    return sys.platform == "win32"


def _resolve_launch_command(*, minimized: bool = True) -> str:
    """Return the full command used to launch NetNeighbor at login."""
    suffix = "--start-minimized-to-tray" if minimized else ""
    if _is_windows():
        import sys as _sys
        exe = Path(_sys.executable)
        root = Path(__file__).resolve().parent.parent
        main_py = root / "main.py"
        if main_py.is_file():
            cmd = f'"{exe}" "{main_py}"'
        else:
            cmd = f'"{exe}"'
        return f"{cmd} {suffix}".strip() if suffix else cmd
    # Linux / macOS: prefer installed wrapper, fall back to python3 + main.py
    w = shutil.which("netneighbor")
    if w:
        return f"{w} {suffix}".strip() if suffix else w
    root = Path(__file__).resolve().parent.parent
    main_py = root / "main.py"
    if main_py.is_file():
        return f'python3 "{main_py}" {suffix}'.strip()
    return f"netneighbor {suffix}".strip()


# ── Windows registry ───────────────────────────────────────────────────────────

def _win_autostart_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY) as key:
            winreg.QueryValueEx(key, _WIN_REG_VALUE)
            return True
    except (FileNotFoundError, OSError):
        return False


def _win_apply_autostart(enabled: bool) -> bool:
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, access=winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                cmd = _resolve_launch_command(minimized=True)
                winreg.SetValueEx(key, _WIN_REG_VALUE, 0, winreg.REG_SZ, cmd)
                _LOG.info("Autostart registered: %s", cmd)
            else:
                try:
                    winreg.DeleteValue(key, _WIN_REG_VALUE)
                    _LOG.info("Autostart removed from registry")
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        _LOG.warning("Could not update autostart registry key: %s", e)
        return False


# ── XDG .desktop (Linux / macOS) ──────────────────────────────────────────────

def _xdg_autostart_file_path() -> Path:
    return Path.home() / ".config" / "autostart" / _AUTOSTART_FILENAME


def _xdg_autostart_enabled() -> bool:
    return _xdg_autostart_file_path().is_file()


def _xdg_apply_autostart(enabled: bool) -> bool:
    path = _xdg_autostart_file_path()
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

    exec_line = _resolve_launch_command(minimized=True)
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


# ── public API (platform-agnostic) ────────────────────────────────────────────

def autostart_file_path() -> Path:
    """XDG path (Linux/macOS only — kept for backwards compat)."""
    return _xdg_autostart_file_path()


def autostart_enabled_on_disk() -> bool:
    """Return True if autostart is currently configured for this user."""
    if _is_windows():
        return _win_autostart_enabled()
    return _xdg_autostart_enabled()


def apply_autostart_pref(enabled: bool) -> bool:
    """Enable or disable launch-at-login. Returns True on success."""
    if _is_windows():
        return _win_apply_autostart(enabled)
    return _xdg_apply_autostart(enabled)
