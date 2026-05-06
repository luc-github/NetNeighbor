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


def setup_i18n() -> None:
    locale_dir = Path(__file__).resolve().parent / "locale"
    languages = _detect_languages()

    # Set the C library locale from the environment so GTK uses it for its own
    # built-in widget labels (dialog buttons "Close", "Yes", "No", etc.).
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass

    # On Ubuntu/Mint, GTK translations live under locale-langpack rather than
    # the standard /usr/share/locale.  Tell GLib about both paths so that GTK's
    # own strings (button labels etc.) are translated.
    for gtk_domain in ("gtk30", "gtk30-properties"):
        for lp in ("/usr/share/locale-langpack", "/usr/share/locale"):
            try:
                locale.bindtextdomain(gtk_domain, lp)  # type: ignore[attr-defined]
            except (AttributeError, OSError):
                break

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
    except FileNotFoundError:
        # No .mo file for the detected language — fall back to English (no-op)
        gettext.install(APP_DOMAIN, localedir=str(locale_dir))
