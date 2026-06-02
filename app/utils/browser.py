# File browser.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Browser utility to open device URLs."""

import logging
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from urllib.parse import urlparse

_LOG = logging.getLogger(__name__)

try:
    import win32netcon
    import win32wnet
    _HAS_WIN32WN = True
except ImportError:
    _HAS_WIN32WN = False

_BROWSER_SCHEMES = {"http", "https", ""}

# Schemes that need a file manager rather than a web browser or xdg-open.
# xdg-open silently fails for smb:// on many Linux desktops (Cinnamon, GNOME, etc.).
_FILE_MANAGER_SCHEMES = {"smb", "ftp", "sftp", "nfs", "dav", "davs"}
_FILE_MANAGERS = ["nemo", "nautilus", "dolphin", "thunar", "pcmanfm"]


def _win32_smb_worker(unc: str) -> None:
    """Background thread: connect via WNet then open explorer."""
    if _HAS_WIN32WN:
        try:
            try:
                win32wnet.WNetCancelConnection2(unc, 0, True)
            except Exception:
                pass
            flags = win32netcon.CONNECT_INTERACTIVE | win32netcon.CONNECT_PROMPT
            win32wnet.WNetAddConnection2(
                win32netcon.RESOURCETYPE_DISK,
                None,
                unc,
                None,
                "",
                "",
                flags,
            )
            _LOG.debug("WNetAddConnection2 ok: %r", unc)
            subprocess.Popen(["explorer.exe", unc])  # noqa: S603,S607
            return
        except Exception as exc:
            if getattr(exc, "winerror", None) == 1223:
                return  # User cancelled — not an error.
            _LOG.warning("WNetAddConnection2 failed (%s): %r", exc, unc)
    # Fallback: no pywin32 or WNet failed — open directly (no credential prompt).
    try:
        subprocess.Popen(["explorer.exe", unc])  # noqa: S603,S607
    except OSError as exc:
        _LOG.warning("explorer.exe also failed: %s", exc)


def _win32_open_smb(unc: str) -> bool:
    """Spawn a background thread to open *unc*, keeping the UI responsive."""
    threading.Thread(target=_win32_smb_worker, args=(unc,), daemon=True).start()
    return True


def open_url(url: str) -> bool:
    if not url:
        return False
    scheme = urlparse(url).scheme.lower()

    if sys.platform == "win32":
        # On Windows, os.startfile opens the default handler (browser for
        # http/https) without ever spawning a console window. webbrowser.open
        # may fall back to a generic controller that runs `cmd /c start`,
        # which flashes an ephemeral terminal in the frozen app — avoid it.
        if scheme == "smb":
            parsed = urlparse(url)
            path = parsed.path.replace("/", "\\")
            unc = f"\\\\{parsed.netloc}{path}"
            return _win32_open_smb(unc)
        if scheme in ("ftp", "sftp"):
            # The default ftp:// handler on modern Windows is the web browser,
            # which dropped FTP support — force Explorer's FTP client instead.
            # explorer.exe is a GUI app, so no console window flashes.
            try:
                subprocess.Popen(["explorer.exe", url])  # noqa: S603,S607
                return True
            except OSError:
                return webbrowser.open(url)
        try:
            os.startfile(url)  # noqa: S606 — URL validated above
            return True
        except OSError:
            return webbrowser.open(url)

    if scheme in _BROWSER_SCHEMES:
        return webbrowser.open(url)

    if sys.platform == "darwin":
        try:
            subprocess.Popen(["open", url])
            return True
        except OSError:
            return webbrowser.open(url)

    # Linux: prefer a known file manager, fall back to xdg-open.
    if scheme in _FILE_MANAGER_SCHEMES:
        for fm in _FILE_MANAGERS:
            if shutil.which(fm):
                try:
                    subprocess.Popen([fm, url], close_fds=True)
                    return True
                except OSError:
                    continue
    try:
        subprocess.Popen(["xdg-open", url], close_fds=True)
        return True
    except FileNotFoundError:
        return webbrowser.open(url)
