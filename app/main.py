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
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app_qt import main
    raise SystemExit(main())
