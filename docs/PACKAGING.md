# Packaging

NetNeighbor ships platform-specific build scripts under `packaging/`.  
**Linux** scripts currently produce **GTK 1.x** packages; **Windows** and **macOS** skeletons target **2.0** via **`app.py`** (PySide6). During migration, builds fall back to `app_qt.py` if `app.py` is not present yet. After the port, follow [`packaging/POST_PORT.md`](../packaging/POST_PORT.md).

Layout: [`packaging/README.md`](../packaging/README.md).

---

## Linux (`packaging/linux/`)

### Prerequisites (build machine)

- Debian/Ubuntu-like environment
- `git`, `dpkg-deb`, `tar`
- Optional: `msgfmt` (`gettext`) for `.po` → `.mo`
- Optional (AppImage): [appimagetool](https://github.com/AppImage/AppImageKit/releases) or `bash packaging/linux/build_appimage.sh --download-tool`

### Build

From repository root:

```bash
chmod +x packaging/linux/*.sh packaging/checksums.sh
./packaging/linux/release.sh
```

Or individual targets:

```bash
./packaging/linux/build_deb.sh
./packaging/linux/build_tarball.sh
./packaging/linux/build_appimage.sh    # optional; needs appimagetool
```

Version defaults to the first line of `VERSION`; override with `./packaging/linux/release.sh 2.0.0`.

### Outputs (`dist/`)

| Artifact | Description |
|----------|-------------|
| `netneighbor_<version>_<arch>.deb` | System install under `/usr/share/netneighbor` |
| `netneighbor-<version>.tar.gz` | Portable tree + `run.sh` |
| `NetNeighbor-<version>-<arch>.AppImage` | Optional; skipped if appimagetool missing |
| `SHA256SUMS-<version>.txt` | Checksums (via `packaging/checksums.sh`) |

### Runtime dependencies (`.deb`, GTK 1.x)

**Depends:** `python3`, `python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-3.0`, `python3-zeroconf`, `samba-common-bin`, AppIndicator bindings.

See `packaging/linux/build_deb.sh` for the full file list and icon layout.

### Install / cleanup

```bash
sudo apt install ./dist/netneighbor_<version>_amd64.deb
netneighbor
sudo apt remove netneighbor
```

```bash
sudo bash packaging/linux/cleanup.sh
bash packaging/linux/cleanup-user-data.sh
```

Tarball: extract, `./run.sh`, optional `./install-desktop.sh`.

---

## Windows (`packaging/windows/`)

### Prerequisites

- Windows 10/11, Python 3.10+
- `pip install -r requirements-qt.txt pyinstaller`
- **Inno Setup 6** (`ISCC.exe` on `PATH`, or set `INNO_SETUP_DIR`)
- **VC++ Redistributable** on end-user machines — see [`QT_DEV_REQUIREMENTS.md`](QT_DEV_REQUIREMENTS.md)

### Build

```powershell
.\packaging\windows\build.ps1
.\packaging\windows\build_installer.ps1
```

Optional: `-Version 2.0.0` on both scripts. Uses `.venv\Scripts\python.exe` when present, else `python`.

### Outputs (`dist/`)

| Artifact | Description |
|----------|-------------|
| `NetNeighbor/` | PyInstaller onedir folder |
| `NetNeighbor-<version>-win64.zip` | Zipped folder |
| `NetNeighbor-<version>-win64-setup.exe` | Inno Setup installer |

PyInstaller spec: `packaging/windows/netneighbor.spec` — entry `app.py` (review `hiddenimports` / `datas` after port).

---

## macOS (`packaging/macos/`)

### Prerequisites

- macOS 12+, Python 3.10+ (venv recommended)
- `pip install -r requirements-qt.txt pyinstaller`
- Xcode Command Line Tools (`xcode-select --install`) if pip/build tools complain

### Build

```bash
bash packaging/macos/build_app.sh
```

### Outputs (`dist/`)

| Artifact | Description |
|----------|-------------|
| `NetNeighbor.app` | Application bundle |
| `NetNeighbor-<version>-macos.zip` | Zipped bundle |

Distribution outside dev machines will need **code signing and notarization** (documented in `POST_PORT.md`).

---

## Checksums (all platforms)

```bash
packaging/checksums.sh <version> [basename ...]
```

Without basenames, includes known patterns already in `dist/`. Linux `release.sh` calls this automatically.

---

## GitHub Actions (release tags)

Workflow [`.github/workflows/release.yml`](../.github/workflows/release.yml) runs on tags `v*` (e.g. `v2.0.0`):

- **Linux** — full `packaging/linux/release.sh`, assets attached to the GitHub Release
- **Windows / macOS** — PyInstaller skeleton builds (`continue-on-error: true` until validated locally)

Push a tag to trigger:

```bash
git tag v2.0.0
git push origin v2.0.0
```

---

## 2.0 migration note

Do not expect Windows/macOS CI builds to be production-ready until:

1. UI port is complete (`app.py`, systray, notification history).
2. [`packaging/POST_PORT.md`](../packaging/POST_PORT.md) checklist is done (`app_qt.py` removed).
3. Local builds pass on each OS; then tighten CI (`continue-on-error: false`).

Linux `.deb` / `tar.gz` / AppImage will switch from GTK to PySide6 in the same pass.
