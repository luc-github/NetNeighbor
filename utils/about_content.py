# File about_content.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Shared About / Credits / License text (GTK V1 and Qt 2.0)."""

from __future__ import annotations

import html
from gettext import gettext as _

GITHUB_PROJECT_URL = "https://github.com/luc-github/NetNeighbor"
LGPL3_LICENSE_URL = "https://www.gnu.org/licenses/lgpl-3.0.html"


def about_program_name() -> str:
    return "NetNeighbor"


def about_comments() -> str:
    return _("Discover and monitor devices on your local network.")


def about_copyright_line() -> str:
    return "Copyright © Luc LEBOSSE"


def about_website_label() -> str:
    return _("GitHub Project")


def about_warranty_line() -> str:
    return _("This program comes with absolutely no warranty.")


def about_license_notice_html() -> str:
    """“See the … for details.” with the license name linked to LGPL-3.0."""
    link_text = _("GNU Lesser General Public License version 3 or later")
    return (
        f"{html.escape(_('See the'))} "
        f'<a href="{html.escape(LGPL3_LICENSE_URL, quote=True)}">'
        f"{html.escape(link_text)}</a> "
        f"{html.escape(_('for details.'))}"
    )


def python_library_credit_entries(*, gtk_ui: bool = False) -> list[tuple[str, str, str]]:
    """(name, description, url) for Credits sections."""
    ui_binding = (
        (
            "PyGObject",
            _("GTK 3 bindings"),
            "https://pygobject.gnome.org",
        )
        if gtk_ui
        else (
            "PySide6",
            _("Qt for Python bindings"),
            "https://doc.qt.io/qtforpython/",
        )
    )
    return [
        ui_binding,
        (
            "zeroconf",
            _("mDNS/DNS-SD discovery"),
            "https://github.com/python-zeroconf/python-zeroconf",
        ),
        (
            "WSDiscovery",
            _("WS-Discovery (Windows devices)"),
            "https://github.com/andreikop/python-ws-discovery",
        ),
    ]


def icon_credit_entries() -> list[tuple[str, str | None]]:
    """(description, optional url) for the Icons credits section."""
    from utils.icon_attribution import icon_credit_lines_for_ui

    entries: list[tuple[str, str | None]] = [
        (line, None) for line in icon_credit_lines_for_ui()
    ]
    entries.append((_("App logo under assets/icons: Luc LEBOSSE"), None))
    return entries


def credits_html() -> str:
    """Rich credits body with clickable links (About dialog Credits page)."""
    parts: list[str] = []

    parts.append(f"<h3>{html.escape(_('Python libraries'))}</h3><ul>")
    for name, desc, url in python_library_credit_entries():
        link = (
            f'<a href="{html.escape(url, quote=True)}">{html.escape(name)}</a>'
        )
        parts.append(f"<li>{link} — {html.escape(desc)}</li>")
    parts.append("</ul>")

    parts.append(f"<h3>{html.escape(_('Icons'))}</h3><ul>")
    for desc, url in icon_credit_entries():
        if url:
            parts.append(
                f'<li><a href="{html.escape(url, quote=True)}">{html.escape(desc)}</a></li>'
            )
        else:
            parts.append(f"<li>{html.escape(desc)}</li>")
    parts.append("</ul>")

    return "\n".join(parts)
