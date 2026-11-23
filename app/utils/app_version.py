# File app_version.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Application release version: single source in repository root ``VERSION``."""

import sys
from pathlib import Path


def _candidate_paths() -> list[Path]:
    """Possible locations of ``VERSION`` depending on how the app is run.

    - PyInstaller bundle (Windows/macOS): ``VERSION`` sits in ``sys._MEIPASS``.
    - Sources / .deb / AppImage / tarball: ``VERSION`` sits next to ``app/``,
      i.e. one level above the directory containing ``utils/``.
    """
    here = Path(__file__).resolve().parent  # .../app/utils
    paths: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        paths.append(Path(meipass) / "VERSION")
    paths.append(here.parent / "VERSION")         # bundle root (PyInstaller) or app/
    paths.append(here.parent.parent / "VERSION")  # project root (sources, Linux packages)
    return paths


def get_app_version(default: str = "0.0.0") -> str:
    """Return the first non-empty, non-comment line from ``VERSION`` at project root."""
    for path in _candidate_paths():
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in raw.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            return s
    return default
