# File main.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Application entry point."""

import multiprocessing
import os
import sys

if __name__ == "__main__":
    # Required for PyInstaller on Windows: prevents frozen child processes from
    # re-running the full app when multiprocessing spawns a worker.
    multiprocessing.freeze_support()
    # PySide6 6.5.x crashes on macOS in QLocale.system() when LANG is unset
    # (apps launched from Finder/Dock don't inherit shell env vars).
    # Detect the system locale; fall back to C.UTF-8 (encoding-safe, language-neutral).
    if sys.platform == "darwin" and not os.environ.get("LANG"):
        import locale as _locale
        try:
            _locale.setlocale(_locale.LC_ALL, "")
            _loc, _enc = _locale.getlocale()
            _lang = f"{_loc}.{_enc}" if _loc else "C.UTF-8"
        except Exception:
            _lang = "C.UTF-8"
        os.environ["LANG"] = _lang
        os.environ.setdefault("LC_ALL", _lang)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app_qt import main
    raise SystemExit(main())
