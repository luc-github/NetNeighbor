# File i18n.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Minimal i18n bootstrap using gettext."""

from __future__ import annotations

import gettext
import locale
import os
from pathlib import Path

APP_DOMAIN = "netneighbor"


def _detect_languages() -> list[str]:
    """Return a priority-ordered list of language codes from the environment.

    Reads LANGUAGE, LC_ALL, LC_MESSAGES, and LANG in that order (same as GNU gettext).
    Each value may contain a colon-separated list (LANGUAGE) or a locale string like
    ``fr_FR.UTF-8``; we normalise them to bare language codes so gettext.find() can
    match the locale directory names (``fr``, ``zh_CN``, …).
    """
    langs: list[str] = []
    seen: set[str] = set()

    def _add(code: str) -> None:
        # Strip encoding suffix (fr_FR.UTF-8 → fr_FR) then try both fr_FR and fr
        code = code.strip().split(".")[0].split("@")[0]
        for candidate in [code, code.split("_")[0]]:
            if candidate and candidate not in seen and candidate.lower() not in ("c", "posix"):
                seen.add(candidate)
                langs.append(candidate)

    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var, "")
        if not val:
            continue
        for part in val.split(":"):
            _add(part)

    # Also ask Python's locale module as a last resort
    try:
        lc = locale.getlocale()[0]
        if lc:
            _add(lc)
    except Exception:
        pass

    return langs


_active_language: str | None = None


def active_language() -> str | None:
    """Return the language code that was actually installed (e.g. ``'zh_CN'``, ``'fr'``), or None for English."""
    return _active_language


def setup_i18n() -> None:
    global _active_language
    locale_dir = Path(__file__).resolve().parent / "locale"
    languages = _detect_languages()

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass

    # Register our locale dir for modules that use `from gettext import gettext as _`.
    gettext.bindtextdomain(APP_DOMAIN, str(locale_dir))
    gettext.textdomain(APP_DOMAIN)

    try:
        translation = gettext.translation(
            APP_DOMAIN,
            localedir=str(locale_dir),
            languages=languages if languages else None,
        )
        translation.install()
        _active_language = languages[0] if languages else None
    except FileNotFoundError:
        # No .mo file for the detected language — fall back to English (no-op)
        gettext.install(APP_DOMAIN, localedir=str(locale_dir))
        _active_language = None
