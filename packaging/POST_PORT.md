# Post-port — packaging checklist

Canonical application entry point: **`app.py`** (PySide6).  
`app_qt.py` is temporary during the UI migration; rename or merge into `app.py` before release packaging is finalized.

Linux scripts under `packaging/linux/` still build **GTK 1.x** payloads until this checklist is done.

After the port (including systray and notification history), consolidate:

- [ ] **`app.py`** — single entry point; remove `app_qt.py` when redundant.
- [ ] **`requirements.txt`** — merge Qt deps (today in `requirements-qt.txt`); freeze for PyInstaller.
- [ ] **PyInstaller `hiddenimports`** — `packaging/windows/netneighbor.spec`, macOS `build_app.sh`.
- [ ] **`datas` / Qt plugins** — `platforms`, `imageformats`, `styles`, `translations` per OS.
- [ ] **Systray + notifications** — extra modules under `utils/` / UI package in specs.
- [ ] **Linux `.deb`** — launcher → `app.py`, tree `ui_qt/` (or merged `ui/`), Depends PySide6.
- [ ] **Linux `tar.gz` / AppImage** — same file list; Qt runtime strategy.
- [ ] **Desktop / `.desktop`** — tray, autostart.
- [ ] **CI** — `continue-on-error: false` on Windows/macOS when local builds pass.
- [ ] **Signing** — Windows Authenticode, Apple notarization (if distributing macOS publicly).

See [`docs/QT_DEV_REQUIREMENTS.md`](../docs/QT_DEV_REQUIREMENTS.md) and [`docs/PACKAGING.md`](../docs/PACKAGING.md).
