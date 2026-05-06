# NetNeighbor

NetNeighbor is a Linux desktop application that replicates and extends the Windows Network
Neighborhood experience.

**Current release:** **0.9.0**

## Current state

- GTK 3 application (single-instance lock, optional demo mode; second launch raises the existing window)
- **SSDP** discovery (multicast listen, M-SEARCH refresh, XML descriptors, offline / TTL handling)
- **mDNS** discovery with `zeroconf` (service browse, host-level aggregation, TXT/services details)
- **WSD** (WS-Discovery) via PyPI `WSDiscovery` — Windows-compatible hosts and printers on UDP 3702
- **NetBIOS** browse via `nmblookup` (Samba **client** tools) and optional **wsdd** cache — see runtime notes below
- Discovery manager with in-memory device cache, cross-protocol merge rules (**MAC** from neighbor cache assists bundle merge), user overrides; details can show **IPv4 from ARP/neigh** when discovery only listed IPv6 for the same MAC
- Main window: list + icon grid, categories, details dialogs, notifications (optional), **View → Preferences** persisted in `~/.config/netneighbor/ui_prefs.json`
- **System tray** (Ayatana/AppIndicator or Gtk.StatusIcon fallback): **Open** / **Minimize to tray** / **Quit**, keep running when closing the window, optional **start minimized**, optional **session autostart**. Autostart `Exec=` adds **`--start-minimized-to-tray`** only in `~/.config/autostart/` — not in the menu launcher — see [`USER_DOCUMENTATION.md`](USER_DOCUMENTATION.md).
- When tray is active: client-side title bar **Maximize + Close** (window minimization to tray is via tray menu or close-with-tray; **F11** toggles fullscreen)
- **Open / double-click** resolves the best connection target in priority order (`http > smb > ssh > ftp > sftp > telnet`); right-click **Open ▶** submenu lists all available targets with labels when multiple are present
- **Per-device commands** (Options tab in device details): dynamic list of connection commands — each entry sets the scheme, optional IP/port override, mode (**Override** replaces the auto-detected default; **Additional** adds an extra entry to the submenu), and optional label. Confirmation dialogs protect Remove / Clear / Reset actions.
- **External applications** (**Tools → External applications…**): per-scheme command templates for Open (`{ip}`, `{port}`, `{name}`, `{type}`, `{category}`, `{url}`) and a global **Custom command** for the right-click menu; **Reset** restores the built-in default per scheme
- App icon: **`assets/svg/netneighbor_icon.svg`** (launcher/window); tray icon adapts to panel theme — white symbolic (`netneighbor-tray-symbolic.svg`) on dark panels, coloured (`netneighbor-tray.svg`) on light panels; selection is automatic at startup via `gtk-theme-name`
- **`.deb`** packaging script: `packaging/build_deb.sh` (see [`docs/PACKAGING.md`](docs/PACKAGING.md))

Developer documentation lives under [`docs/README.md`](docs/README.md).
User documentation lives in [`USER_DOCUMENTATION.md`](USER_DOCUMENTATION.md).

## Autodetection (limits) and corrections

Device **names**, **types**, and related hints come from passive discovery (SSDP, mDNS, and cached
profiles on disk). Heuristics reflect what your LAN advertises; they are **not infallible** and may
differ on another network or firmware revision.

When the UI does not match reality, NetNeighbor provides:

1. **Per-device overrides** — display **name**, **type**, **location**, and **icon** choices (stored in
   user preferences) are applied **after** discovery and override protocol-derived values for that
   device identity.

2. **User rule overlays** — optional pattern rules under `~/.config/netneighbor/` (mDNS and SSDP)
   adjust classification and labels for recurring equipment. Rules that prove stable and broadly useful
   can be **contributed upstream** as bundled defaults so they apply for everyone.

Merge ordering between discovery inputs (`discovery.json`, including `merge.information_precedence`) is
described for contributors in [`docs/MAINTENANCE.md`](docs/MAINTENANCE.md).

## Requirements

- Linux desktop with GTK3 runtime
- Python 3.10+
- `python3-gi` and GTK bindings installed from distro packages

### Linux Mint / Ubuntu / Debian prerequisites

Install GTK/PyGObject and Cairo from system packages:

```bash
sudo apt update
sudo apt install -y python3-gi python3-gi-cairo gir1.2-gtk-3.0 libcairo2-dev pkg-config
```

**Recommended on desktop** (NetBIOS names + system tray):

```bash
sudo apt install -y samba-common-bin
sudo apt install -y gir1.2-ayatanaappindicator3-0.1   # typical on Mint/Ubuntu
# or, on some setups: gir1.2-appindicator3-0.1
```

NetNeighbor does **not** require Samba server daemons (`smbd` / `nmbd`) — only the **`nmblookup`** client from `samba-common-bin`.

Python dependencies installed in the virtual environment:
- `zeroconf`
- `WSDiscovery` (optional; graceful degradation if absent)

## Run (development)

```bash
python -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Demo data is enabled by default to help with visual mockups. Disable it with:

```bash
NETNEIGHBOR_DEMO=0 python main.py
```

## Translations (i18n)

Translation catalogs are available under `locale/`:

| Language | Code | Status |
|----------|------|--------|
| French | `fr` | Partial (~150 untranslated strings) |
| Italian | `it` | Ready for translation |
| Spanish | `es` | Ready for translation |
| German | `de` | Ready for translation |
| Dutch | `nl` | Ready for translation |
| Traditional Chinese (Taiwan) | `zh_TW` | Ready for translation |
| Simplified Chinese | `zh_CN` | Ready for translation |
| Japanese | `ja` | Ready for translation |

Compile translations after edits:

```bash
msgfmt locale/fr/LC_MESSAGES/netneighbor.po -o locale/fr/LC_MESSAGES/netneighbor.mo
```

Run app in French for testing:

```bash
LANG=fr_FR.UTF-8 python main.py
```

Full translation workflow (new language, POT merge/update, runtime checks):
[`docs/I18N.md`](docs/I18N.md).

Only one NetNeighbor instance is allowed at a time on Linux to avoid discovery conflicts.
A second launch asks the first instance to bring its window to the foreground.

## Pending work

See [`docs/TODO.md`](docs/TODO.md) for known items and future ideas.

Contribution and maintenance reminders: [`docs/MAINTENANCE.md`](docs/MAINTENANCE.md), user overlays: [`docs/COMMUNITY_OVERRIDES.md`](docs/COMMUNITY_OVERRIDES.md).
