# File session_autostart.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Session-login autostart: XDG .desktop (Linux) / LaunchAgents plist (macOS) / registry Run key (Windows)."""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

_LOG = logging.getLogger(__name__)

_AUTOSTART_FILENAME = "io.esp3d.netneighbor.desktop"
_WIN_REG_VALUE = "NetNeighbor"
_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_MACOS_LAUNCHAGENT_LABEL = "io.esp3d.netneighbor"

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


def _resolve_launch_argv(*, minimized: bool = True) -> list[str]:
    """Return the argv list for the LaunchAgent ProgramArguments key (macOS)."""
    suffix = "--start-minimized-to-tray" if minimized else None
    # Frozen PyInstaller bundle: sys.executable IS the stable binary path
    # (e.g. /Applications/NetNeighbor.app/Contents/MacOS/NetNeighbor).
    # Do NOT reference main.py — it lives in a per-run _MEI temp dir.
    if getattr(sys, "frozen", False):
        argv = [sys.executable]
        if suffix:
            argv.append(suffix)
        return argv
    w = shutil.which("netneighbor")
    if w:
        argv = [w]
    else:
        root = Path(__file__).resolve().parent.parent
        main_py = root / "main.py"
        argv = [sys.executable, str(main_py)] if main_py.is_file() else [sys.executable]
    if suffix:
        argv.append(suffix)
    return argv


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


# ── LaunchAgents plist (macOS) ────────────────────────────────────────────────

def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_MACOS_LAUNCHAGENT_LABEL}.plist"


def _macos_autostart_enabled() -> bool:
    return _macos_plist_path().is_file()


def _macos_launchctl(action: str, path: Path) -> None:
    """Run launchctl load/unload for the given plist (best-effort, errors are logged)."""
    import subprocess
    import os
    try:
        if action == "load":
            # macOS 11+: bootstrap; fall back to legacy load -w
            uid = os.getuid()
            r = subprocess.run(
                ["launchctl", "bootstrap", f"gui/{uid}", str(path)],
                capture_output=True,
            )
            if r.returncode != 0:
                subprocess.run(["launchctl", "load", "-w", str(path)], capture_output=True)
        else:
            uid = os.getuid()
            r = subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}", str(path)],
                capture_output=True,
            )
            if r.returncode != 0:
                subprocess.run(["launchctl", "unload", "-w", str(path)], capture_output=True)
    except OSError as e:
        _LOG.warning("launchctl %s failed: %s", action, e)


def _macos_apply_autostart(enabled: bool) -> bool:
    path = _macos_plist_path()
    if not enabled:
        _macos_launchctl("unload", path)
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            _LOG.warning("Could not remove LaunchAgent plist %s: %s", path, e)
            return not path.exists()
        return True

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _LOG.warning("Could not create LaunchAgents directory: %s", e)
        return False

    argv = _resolve_launch_argv(minimized=True)
    args_xml = "\n".join(f"        <string>{a}</string>" for a in argv)
    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
        ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f"    <key>Label</key><string>{_MACOS_LAUNCHAGENT_LABEL}</string>\n"
        f"    <key>ProgramArguments</key>\n    <array>\n{args_xml}\n    </array>\n"
        "    <key>RunAtLoad</key><true/>\n"
        "    <key>KeepAlive</key><false/>\n"
        "</dict></plist>\n"
    )
    try:
        path.write_text(plist, encoding="utf-8")
        path.chmod(0o644)
        _LOG.info("LaunchAgent written: %s", path)
    except OSError as e:
        _LOG.warning("Could not write LaunchAgent plist %s: %s", path, e)
        return False
    _macos_launchctl("load", path)
    return True


# ── XDG .desktop (Linux) ──────────────────────────────────────────────────────

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
    if sys.platform == "darwin":
        return _macos_autostart_enabled()
    return _xdg_autostart_enabled()


def apply_autostart_pref(enabled: bool) -> bool:
    """Enable or disable launch-at-login. Returns True on success."""
    if _is_windows():
        return _win_apply_autostart(enabled)
    if sys.platform == "darwin":
        return _macos_apply_autostart(enabled)
    return _xdg_apply_autostart(enabled)
