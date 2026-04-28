"""Browser utility to open device URLs."""

import webbrowser


def open_url(url: str) -> bool:
    if not url:
        return False
    return webbrowser.open(url)
