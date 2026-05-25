# NetNeighbor

NetNeighbor discovers and monitors devices on your local network — a modern equivalent of the Windows Network Neighborhood experience. Runs on Windows, Linux and macOS.

**Current release: 2.0.0**

![NetNeighbor main window](docs/images/overview-hero.png)

## Features

- **Automatic discovery** — SSDP, mDNS/Bonjour, WS-Discovery, NetBIOS; no active port scan
- **Instant startup** — previously-seen devices appear from cache in < 100 ms
- **List and icon grid** views with sidebar categories (by type or location)
- **Open devices** in one click — HTTP, HTTPS, SMB, SSH, FTP, SFTP, Telnet with configurable priority
- **Per-device overrides** — custom name, type, location, icon, and connection commands
- **Icon packs** — switch between icon themes; install community packs or create your own
- **System tray** — minimize to tray, start minimized, session autostart
- **Localised** — French, Spanish, German, Italian, Dutch, Japanese, Chinese (Simplified/Traditional)
- **Packaged** as `.exe` + installer (Windows), `.dmg` (macOS), `.deb` (Ubuntu/Mint/Debian), `.AppImage`, and tarball

> [!WARNING]
>### Disclaimer
> The software is provided 'as is,' without any warranty of any kind, expressed or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose, and non-infringement. In no event shall the authors or copyright holders be liable for any claim, damages, or other liability, whether in an action of contract, tort, or otherwise, arising from, out of, or in connection with the software or the use or other dealings in the software.
>It is essential that you carefully read and understand this disclaimer before using this software and its components. If you do not agree with any part of this disclaimer, please refrain from using the software.  


## Download

Latest release: [GitHub Releases](https://github.com/luc-github/NetNeighbor/releases)

Available formats: `.exe` · `.dmg` (Apple Silicon & Intel) · `.deb` · `.AppImage` · `.tar.gz`

## Requirements

| Platform | Requirement |
|----------|-------------|
| Windows | Windows 10 or later |
| Linux | Ubuntu 22.04+, Linux Mint 21+, Debian 12+, or equivalent |
| macOS (Apple Silicon) | macOS 12 (Monterey) or later |
| macOS (Intel) | macOS 11 (Big Sur) or later |
| From source | Python 3.10+ · PySide6 6.5+ |

## Install

**Windows installer:**
```
NetNeighbor-2.0.0-win64-setup.exe
```

> **Note:** the installer is not code-signed. Windows Defender SmartScreen may show a warning — click **More info** → **Run anyway**.

**macOS:**

Open `NetNeighbor-2.0.0-macos-arm64.dmg` (Apple Silicon) or `NetNeighbor-2.0.0-macos-intel.dmg` (Intel), then drag NetNeighbor to Applications.

> **Note:** the app is not notarized. On first launch Gatekeeper may block it — right-click → **Open** → **Open**, or run `xattr -cr /Applications/NetNeighbor.app` in Terminal.

**`.deb` package (recommended, Linux):**
```bash
sudo dpkg -i netneighbor_2.0.0_amd64.deb
```

**`.AppImage` (Linux):**
```bash
chmod +x NetNeighbor-2.0.0-x86_64.AppImage
./NetNeighbor-2.0.0-x86_64.AppImage
```

**Tarball (Linux):**
```bash
tar -xzf netneighbor-2.0.0.tar.gz
cd netneighbor-2.0.0
bash install-user-desktop.sh   # optional: add menu shortcut
bash run.sh
```

## Run from source

**Linux / macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

**Windows:**
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Documentation

User documentation: [`USER_DOCUMENTATION.md`](docs/USER_DOCUMENTATION.md)

Developer documentation: [`docs/README.md`](docs/README.md)

## Translations

| Language | Code | Status |
|----------|------|--------|
| French | `fr` | Complete |
| Italian | `it` | Complete |
| Spanish | `es` | Complete |
| German | `de` | Complete |
| Dutch | `nl` | Complete |
| Traditional Chinese | `zh_TW` | Complete |
| Simplified Chinese | `zh_CN` | Complete |
| Japanese | `ja` | Complete |

Compile after editing a `.po` file:
```bash
msgfmt app/locale/fr/LC_MESSAGES/netneighbor.po -o app/locale/fr/LC_MESSAGES/netneighbor.mo
```

Test in a specific language:
```bash
LANG=fr_FR.UTF-8 python app/main.py
```

Full i18n workflow: [`docs/contributing/I18N.md`](docs/contributing/I18N.md)

## Icon packs

Five community icon packs are available in [`icons_packs/`](icons_packs/):

| Pack | Style |
|------|-------|
| [Clay 3D](icons_packs/clay3d/) | Colourful 3D clay-style icons |
| [Dark RGB](icons_packs/darkrgb/) | Dark theme with RGB accent colours |
| [OSX Aqua](icons_packs/osx_aqua/) | macOS Aqua-inspired look |
| [White Frost](icons_packs/whitefrost/) | Flat white / frosted glass |
| [Windows 11 Fluent](icons_packs/win11fluent/) | Windows 11 Fluent Design |

Copy a pack folder to `~/.config/netneighbor_icon_packs/`, rename `infopack.json` → `iconpack.json` and `{name}_preview.png` → `preview.png`, then select it in **Preferences → General → Icon pack**.

Full instructions and pack format: [`icons_packs/README.md`](icons_packs/README.md)

---

## Autodetection and corrections

Device names and types are inferred from protocol announcements — they are best-effort.
Use right-click → **Type / Rename / Location / Icon** for per-device corrections.
Community rule overlays: [`docs/contributing/COMMUNITY_OVERRIDES.md`](docs/contributing/COMMUNITY_OVERRIDES.md).

## Pending work and roadmap

[`docs/ROADMAP.md`](docs/ROADMAP.md)

Contribution and maintenance notes: [`docs/operations/MAINTENANCE.md`](docs/operations/MAINTENANCE.md)
