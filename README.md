# NetNeighbor

NetNeighbor is a Linux desktop application that replicates and extends the Windows Network
Neighborhood experience.

## Current state

- GTK 3 application (single-instance lock, optional demo mode)
- **SSDP** discovery (multicast listen, M-SEARCH refresh, XML descriptors, offline / TTL handling)
- **mDNS** provider is still a stub; real `zeroconf` browsing is the next milestone
- Discovery manager with in-memory device cache, SSDP merge rules, user overrides
- Main window: list + icon grid, categories, details dialogs, notifications (optional), preferences in `~/.config/netneighbor/ui_prefs.json`

Developer documentation lives under [`docs/README.md`](docs/README.md).
User documentation (MVP) lives in [`USER_DOCUMENTATION.md`](USER_DOCUMENTATION.md).

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

Python dependencies installed in the virtual environment:
- `zeroconf`

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

French catalog is available in `locale/fr/LC_MESSAGES/netneighbor.po`.

Compile translations after edits:

```bash
msgfmt locale/fr/LC_MESSAGES/netneighbor.po -o locale/fr/LC_MESSAGES/netneighbor.mo
```

Run app in French for testing:

```bash
LANG=fr_FR.UTF-8 python main.py
```

Only one NetNeighbor instance is allowed at a time on Linux to avoid discovery conflicts.
A second launch asks the first instance to bring its window to the foreground.

## Next implementation milestones

1. Implement real mDNS discovery with `zeroconf` (service browser, TXT/SRV → `Device`)
2. Refine cross-protocol deduplication once SSDP + mDNS both emit live data
3. Packaging (AppImage / `.deb`) and release checklist — see [`docs/ROADMAP.md`](docs/ROADMAP.md)
