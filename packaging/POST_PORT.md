# Post-port — packaging checklist

Canonical application entry point: **`app/main.py`** — inside `app/`, `sys.path` not needed; imports `app_qt.main()` directly.

- [x] **`app/`** — all Python source packages + assets/config/locale (`app_qt.py`, `i18n.py`, `discovery/`, `model/`, `ui/`, `utils/`, `assets/`, `config/`, `locale/`).
- [x] **`requirements.txt`** — PySide6 + base requirements; used by postinst pip install.
- [x] **PyInstaller `hiddenimports`** — updated in `packaging/windows/netneighbor.spec` (QtCore, QtGui, QtWidgets, QtNetwork, QtSvg, zeroconf, WSDiscovery); `pathex` includes `app/`.
- [ ] **`datas` / Qt plugins** — `platforms`, `imageformats`, `styles`, `translations` per OS — validate after first PyInstaller run.
- [x] **Linux `.deb`** — launcher uses `app/main.py`, copies `app/`, Depends updated to `python3 (>= 3.10), python3-pip, samba-common-bin`; postinst pip-installs PySide6.
- [x] **Linux `tar.gz` / AppImage** — file lists updated (`app/`, `requirements.txt`).
- [ ] **Desktop / `.desktop`** — verify tray and autostart work after install.
- [ ] **CI** — `continue-on-error: false` on Windows/macOS when local builds pass.
- [ ] **Signing** — Windows Authenticode, Apple notarization — not planned (cost prohibitive for an open-source project; users may see OS security warnings on first run).

See [`docs/operations/PACKAGING.md`](../docs/operations/PACKAGING.md).
