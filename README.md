# NetNeighbor

NetNeighbor is a Linux desktop application that replicates and extends the Windows Network
Neighborhood experience.

## Current state

This repository now includes a runnable project skeleton:
- GTK application bootstrap
- Discovery manager with SSDP/mDNS provider interfaces
- Basic device model
- Main window with refresh button and device list widget
- Initial `device_types.json` and icon contribution guide

Current SSDP and mDNS providers are placeholders and do not yet perform real network discovery.

## Requirements

- Linux desktop with GTK3 runtime
- Python 3.10+
- `python3-gi` and GTK bindings installed from distro packages

Python dependencies:
- `PyGObject`
- `zeroconf`

## Run (development)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Demo data is enabled by default to help with visual mockups. Disable it with:

```bash
NETNEIGHBOR_DEMO=0 python main.py
```

Only one NetNeighbor instance is allowed at a time on Linux to avoid discovery conflicts.
A second launch asks the first instance to bring its window to the foreground.

## Next implementation milestones

1. Implement real mDNS discovery with `zeroconf`
2. Implement real SSDP UDP multicast listener/query
3. Add deduplication and online/offline lifecycle
4. Add persistence in `~/.config/netneighbor/devices.json`
