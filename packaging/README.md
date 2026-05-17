# Packaging layout

| Path | Role |
|------|------|
| [`linux/`](linux/) | `.deb`, `tar.gz`, AppImage, `release.sh` (GTK 1.x until Qt port) |
| [`windows/`](windows/) | PyInstaller → `.exe`, Inno Setup → installer (Qt skeleton) |
| [`macos/`](macos/) | PyInstaller → `.app` bundle (Qt skeleton) |
| [`checksums.sh`](checksums.sh) | `SHA256SUMS-<version>.txt` for `dist/` artifacts |
| [`POST_PORT.md`](POST_PORT.md) | Checklist after UI port (`app.py`, deps, Linux packages) |

Full documentation: [`docs/PACKAGING.md`](../docs/PACKAGING.md).

## Quick start

**Linux (Debian/Ubuntu):**

```bash
chmod +x packaging/linux/*.sh packaging/checksums.sh
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
