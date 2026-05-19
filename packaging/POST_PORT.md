# Post-port — packaging checklist

Canonical application entry point: **`main.py`** → imports `app_qt.py`.

- [x] **`main.py`** — single entry point, imports `app_qt.main()`.
- [x] **`requirements-qt.txt`** — PySide6 + base requirements; used by postinst pip install.
- [x] **PyInstaller `hiddenimports`** — updated in `packaging/windows/netneighbor.spec` (QtCore, QtGui, QtWidgets, QtNetwork, QtSvg, zeroconf, WSDiscovery).
- [ ] **`datas` / Qt plugins** — `platforms`, `imageformats`, `styles`, `translations` per OS — validate after first PyInstaller run.
- [x] **Linux `.deb`** — launcher uses `main.py`, copies `ui/` + `app_qt.py`, Depends updated to `python3 (>= 3.10), python3-pip, samba-common-bin`; postinst pip-installs PySide6.
- [x] **Linux `tar.gz` / AppImage** — file lists updated (ui/, app_qt.py, requirements-qt.txt).
- [ ] **Desktop / `.desktop`** — verify tray and autostart work after install.
- [ ] **CI** — `continue-on-error: false` on Windows/macOS when local builds pass.
- [ ] **Signing** — Windows Authenticode, Apple notarization (if distributing macOS publicly).

See [`docs/QT_DEV_REQUIREMENTS.md`](../docs/QT_DEV_REQUIREMENTS.md) and [`docs/PACKAGING.md`](../docs/PACKAGING.md).
