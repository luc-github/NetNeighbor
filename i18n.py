"""Minimal i18n bootstrap using gettext."""

from __future__ import annotations

import gettext
from pathlib import Path

APP_DOMAIN = "netneighbor"


def setup_i18n() -> None:
    locale_dir = Path(__file__).resolve().parent / "locale"
    gettext.bindtextdomain(APP_DOMAIN, str(locale_dir))
    gettext.textdomain(APP_DOMAIN)
    gettext.install(APP_DOMAIN, localedir=str(locale_dir))

