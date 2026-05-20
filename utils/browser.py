# File browser.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Browser utility to open device URLs."""

import os
import shutil
import subprocess
import sys
import webbrowser
from urllib.parse import urlparse

_BROWSER_SCHEMES = {"http", "https", ""}

# Schemes that need a file manager rather than a web browser or xdg-open.
# xdg-open silently fails for smb:// on many Linux desktops (Cinnamon, GNOME, etc.).
_FILE_MANAGER_SCHEMES = {"smb", "ftp", "sftp", "nfs", "dav", "davs"}
_FILE_MANAGERS = ["nemo", "nautilus", "dolphin", "thunar", "pcmanfm"]


def open_url(url: str) -> bool:
    if not url:
        return False
    scheme = urlparse(url).scheme.lower()
    if scheme in _BROWSER_SCHEMES:
        return webbrowser.open(url)

    if sys.platform == "win32":
        try:
            os.startfile(url)  # noqa: S606 — URL validated above
            return True
        except OSError:
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
