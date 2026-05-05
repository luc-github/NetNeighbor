"""Browser utility to open device URLs."""

import shutil
import subprocess
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
