# Packaging

NetNeighbor 2.0 ships platform-specific build scripts under `packaging/` — all targets use **PySide6** (`app_qt.py`).

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
chmod +x packaging/linux/*.sh
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
| `SHA256SUMS-<version>.txt` | Checksums (via `packaging/linux/checksums.sh`) |

### Runtime dependencies (`.deb`)

**Depends:** `python3 (>= 3.10)`, `python3-pip`, `samba-common-bin` (NetBIOS name resolution).  
PySide6, zeroconf, and WSDiscovery are installed via pip into the package prefix — no system GUI library dependencies.

See `packaging/linux/build_deb.sh` for the full file list and icon layout.

### Bundled icon set — size trimming

`assets/icons/netneighbor/` is ~151 MB in the source tree (10 resolution
directories). All three Linux build scripts automatically trim the directory to the
5 sizes used by the Qt icon-view presets (16, 32, 48, 96, 256), reducing the packaged
size by ~143 MB. The Windows build does the same after the PyInstaller `onedir` step.
See [`MAINTENANCE.md`](MAINTENANCE.md#bundled-device-icons-netneighbor) for details.

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
- `pip install -r requirements.txt pyinstaller`
- **Inno Setup 6** (`ISCC.exe` on `PATH`, or `winget install JRSoftware.InnoSetup --source winget`)
- **VC++ Redistributable** on end-user machines (PySide6 requires it; Windows 10/11 typically ship it already)

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

PyInstaller spec: `packaging/windows/netneighbor.spec` — entry `app/main.py`, `pathex` includes `app/`.

---

### ⚠️ CRITICAL — Windows PyInstaller gotchas (read before any build)

Two bugs that make the frozen app behave like malware if not addressed. Both are fixed
in the current codebase; **do not remove or move these fixes**.

#### Bug 1 — `multiprocessing.freeze_support()` missing → hundreds of windows

**Symptom:** Launching the `.exe` immediately opens dozens or hundreds of semi-transparent
windows that cannot be closed. Task Manager shows the process tree growing out of control.
Killing the main process leaves orphaned child-process windows on screen.

**Root cause:** On Windows, PyInstaller frozen apps use the `spawn` multiprocessing start
method. When any dependency (zeroconf, ThreadPoolExecutor, …) spawns a child process,
Windows re-executes the frozen `.exe` from scratch. Without `freeze_support()` the child
re-runs the full application — which spawns another child — infinitely.

**Fix (in `main.py`):**
```python
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()   # ← MUST be first, before any other code
    from app_qt import main
    raise SystemExit(main())
```

`freeze_support()` detects the special command-line token that PyInstaller injects for
child processes and calls `sys.exit()` immediately, preventing the full app from running.

**Rule:** `freeze_support()` must remain the very first statement inside
`if __name__ == "__main__":`, before any import or application code.

---

#### Bug 2 — `subprocess` calls without `CREATE_NO_WINDOW` → console window spam

**Symptom:** Many black or semi-transparent console (terminal) windows flash on screen
every few seconds while the app is running. Each window appears and disappears rapidly.

**Root cause:** When a frozen app built with `console=False` calls `subprocess.run()` or
`subprocess.Popen()` without `creationflags=subprocess.CREATE_NO_WINDOW`, Windows creates
a visible console window for every subprocess. NetNeighbor looks up MAC addresses via
`arp -a` for each discovered device on every UI refresh — potentially 10–30 calls per
cycle — producing a storm of console flashes.

**Fix (in `utils/neighbor_mac.py`):**
```python
_cflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
proc = subprocess.run([...], creationflags=_cflags, ...)
```

A 850 ms cache on the `arp -a` output also prevents redundant subprocess calls within
the same refresh cycle (one `arp -a` covers all devices).

**Rule:** Every `subprocess.run()` / `Popen()` call in Windows-reachable code paths must
pass `creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)`. The `getattr` fallback
keeps the code portable (the constant is Windows-only). `discovery/netbios.py` already
uses `_subprocess_no_window_kwargs()` for this — follow the same pattern.

---

## macOS (`packaging/macos/`)

Two separate builds are produced: one for Apple Silicon (arm64) and one for Intel (x86_64).

### Prerequisites

**Homebrew** (required for `create-dmg` — both architectures) :

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install create-dmg
```

Homebrew requires Xcode Command Line Tools; the installer will prompt to install them automatically if missing.

---

**Apple Silicon (arm64) — macOS 12+**
- Homebrew + `create-dmg` (see above)
- Python 3.10+, `pip install -r requirements.txt pyinstaller`

**Intel (x86_64) — macOS 11 Big Sur+**
- Homebrew + `create-dmg` (see above)
- **Python 3.11.x** — PySide6 6.5.x requires Python < 3.12; Python 3.12+ can only install PySide6 6.6+ which requires macOS 12+. Download from [python.org](https://www.python.org/downloads/).
- `pip install "PySide6>=6.5,<6.6" -r requirements.txt pyinstaller`
  PySide6 6.5.x is the last series supporting macOS 11. PySide6 6.6+ requires macOS 12+.

CI uses `macos-15-intel` runner (Intel x86_64, macOS 15) — faster queue than the deprecated `macos-13`.

### Build

```bash
# Apple Silicon
bash packaging/macos/build_app.sh 2.0.0 arm64

# Intel (run on an Intel Mac or macos-13 CI runner)
bash packaging/macos/build_app.sh 2.0.0 x86_64

# Native arch (detects automatically)
bash packaging/macos/build_app.sh 2.0.0
```

### DMG background images

`packaging/macos/dmg_background.png` (560×300 px) and `dmg_background@2x.png` (1120×600 px) are the drag-to-Applications background shown in the Finder window.

**Important — macOS DPI:** these files must be saved at **72 DPI** (not 96 DPI which is the Windows/Linux default). At 96 DPI macOS renders the image at 75% of its pixel size (560 px → 420 px), leaving a gray border around the background. Most image editors have a resolution/DPI setting in the export dialog — set it to 72.

To verify or fix on macOS:
```bash
sips -g dpiWidth packaging/macos/dmg_background.png        # must show 72.000
# Fix if needed (sips -s takes px/cm, not DPI: 72 DPI = 28.346 px/cm):
sips -s dpiWidth 28.346 -s dpiHeight 28.346 packaging/macos/dmg_background.png
sips -s dpiWidth 56.693 -s dpiHeight 56.693 packaging/macos/dmg_background@2x.png
```

### Outputs (`dist/`)

| Artifact | Description |
|----------|-------------|
| `NetNeighbor.app` | Application bundle (intermediate, deleted by CI after smoke test) |
| `NetNeighbor-<version>-macos-arm64.dmg` | Apple Silicon installer DMG (macOS 12+) |
| `NetNeighbor-<version>-macos-intel.dmg` | Intel installer DMG (macOS 11 Big Sur+) |

### Local testing on a Big Sur Intel VM (VirtualBox)

The Intel build can be built and tested directly on a Big Sur machine:

```bash
# 1. Install Homebrew (includes Xcode Command Line Tools if missing)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install create-dmg

# 2. Install Python 3.11 from python.org, then:
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install "PySide6>=6.5,<6.6" -r requirements.txt pyinstaller

# 3. Build
bash packaging/macos/build_app.sh 2.0.0 x86_64
# → dist/NetNeighbor-2.0.0-macos-intel.dmg
```

Code signing (Apple notarization) is not planned — cost prohibitive for an open-source project. Users may see an OS security warning on first run; this is expected and harmless.

---

## Checksums (all platforms)

```bash
packaging/linux/checksums.sh <version> [basename ...]
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
