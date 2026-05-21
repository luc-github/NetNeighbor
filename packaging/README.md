# Packaging layout

| Path | Role |
|------|------|
| [`linux/`](linux/) | `.deb`, `tar.gz`, AppImage, `release.sh`, `checksums.sh` (PySide6/Qt, 2.0) |
| [`windows/`](windows/) | PyInstaller → `.exe`, Inno Setup → installer |
| [`macos/`](macos/) | PyInstaller → `.app` bundle |
| [`POST_PORT.md`](POST_PORT.md) | Checklist after UI port (`app.py`, deps, Linux packages) |

Full documentation: [`docs/PACKAGING.md`](../docs/PACKAGING.md).

## Quick start

**Linux (Debian/Ubuntu):**

```bash
chmod +x packaging/linux/*.sh
./packaging/linux/release.sh
```

**Windows (dev machine):**

```powershell
.\packaging\windows\build.ps1
.\packaging\windows\build_installer.ps1
```

**macOS:**

```bash
bash packaging/macos/build_app.sh
```
