# NetNeighbor — Project Brief v1.4

**Supersedes:** architecture and roadmap sections of v1.3 (kept in repo for history).  
**Developer index:** [`docs/README.md`](README.md)

## Changes from v1.3

- Documents **current codebase**: SSDP and mDNS implemented (zeroconf-based mDNS with multi-service host aggregation).
- Persistence described as **`~/.config/netneighbor/ui_prefs.json`** (UI state, overrides, monitored device snapshots), not a separate `devices.json` device database.
- Features and roadmap aligned with [`ROADMAP.md`](ROADMAP.md).

## Description

NetNeighbor is a Linux desktop application that replicates and extends the Windows Network
Neighborhood experience, using SSDP and mDNS protocols to discover and display local network
devices in a user-friendly GUI.

## Goals

- Provide a simple, native-feeling GUI for local network device discovery on Linux
- Support SSDP (UPnP) and mDNS discovery protocols
- Target ESP32/ESP8266 devices (ESP3D ecosystem) as primary use case, while remaining generic
- Extensible architecture — new protocols can be added without rewriting existing code

## Target Platform

- Linux (primary: Linux Mint / Ubuntu / Debian)
- Python 3 + GTK3 + PyGObject
- Distributable as AppImage / `.deb`

---

## Scope

### In scope (MVP)

- SSDP (UPnP multicast) discovery — **implemented**
- mDNS (zeroconf) discovery — **implemented**
- PC detection via WSD/SSDP (`urn:schemas-microsoft-com:device:Computer:1`) — subject to devices on network
- PC detection via `_smb._tcp` mDNS

### Out of scope (future protocols)

- NetBIOS/LLMNR discovery
- Active network scan (no ping sweep, no ARP scan)
- Linux PC detection without Avahi/Samba
- Proprietary protocols (Sonos, Philips Hue, etc.)
- SMB share authentication / access
- IPv6 support

> **Rule:** NetNeighbor listens and displays what announces itself.  
> It does not actively search for what is hidden.  
> New protocols will be added in future versions via the discovery API.

---

## Architecture (current tree)

```
netneighbor/
├── main.py                  # Entry point
├── app.py                   # Gtk.Application, single-instance, DiscoveryManager, MainWindow
│
├── discovery/
│   ├── base.py              # BaseDiscovery — protocol callback contract
│   ├── manager.py           # DiscoveryManager — cache, merges, overrides, listeners
│   ├── ssdp.py              # SSDP UDP multicast + XML descriptors + rules
│   └── mdns.py              # mDNS discovery (zeroconf)
│
├── model/
│   └── device.py            # Device dataclass + stable SSDP key
│
├── ui/
│   ├── main_window.py       # Shell, menus, prefs, notifications, GLib.idle_add bridge
│   ├── device_list.py       # List + icon grid, bundles by (ip, port), context menus
│   ├── device_details.py    # Details dialog
│   └── icons.py             # Icon resolver
│
├── assets/icons/            # Bundled PNG/SVG
├── data/device_types.json   # Optional mapping (icons / labels)
├── config/
│   ├── ssdp_rules.json      # Heuristic SSDP name / type / information rules (shipped)
│   └── mdns_rules.json      # Heuristic mDNS TXT summary + optional type_rules (shipped)
├── utils/
│   ├── browser.py
│   ├── details_payload.py   # SSDP/mDNS detail panels
│   ├── ui_prefs.py          # ~/.config/netneighbor/ui_prefs.json
│   └── notifications.py     # Desktop notifications (best-effort)
├── docs/                    # Developer docs (README index, SSDP, UI, roadmap, …)
├── requirements.txt
├── README.md
└── LICENSE
```

**Runtime config (user):** `~/.config/netneighbor/` — `ui_prefs.json` (UI + overrides); optional **`device_types.json`**, **`ssdp_rules.json`**, **`mdns_rules.json`** merged with bundled defaults (`COMMUNITY_OVERRIDES.md`).  
**Optional log:** `~/.cache/netneighbor/netneighbor.log` when file logging is enabled.

---

## Discovery API

See `discovery/base.py` — `start`, `stop`, `refresh`, `set_callback`.  
Protocols emit `("device", payload_dict)`; `DiscoveryManager` builds `Device` instances and notifies UI listeners.

---

## Data flow

```
SSDPDiscovery / MDNSDiscovery
        │
        ▼
DiscoveryManager (in-memory dict by Device.key)
        │
        ├── user overrides: type, monitored, last_seen (from ui_prefs)
        │
        ▼
MainWindow listener → GLib.idle_add → DeviceList (list + icon grid, categories)
```

---

## Device model

```text
Device:
    name, ip, port, type, category, source
    url: optional presentation URL
    metadata: dict — SSDP XML fields / raw XML; mDNS TXT + discovered services
    last_seen: datetime
    online: bool
    monitored: bool   # user follow; grey when offline and monitored
    icon: str | None
```

Stable **`Device.key`**: for SSDP, prefer `UDN`, then `USN` root, then MAC, else `ssdp:ip:port` style endpoint key; other sources use `source:ip:port`.

---

## Device categories (UI)

Same intent as v1.3 — actual classification comes from inferred `type` and overrides.

---

## Device types database (`data/device_types.json`)

Still used for icon/type hints where applicable. See embedded example in v1.3 or the live JSON file.

---

## Features

### Implemented (baseline)

- SSDP multicast discovery, M-SEARCH refresh, NOTIFY handling, XML descriptors
- Offline policy: NOTIFY byebye immediate; TTL timeout uses **`2 × max-age`** when present (bounded)
- Configurable SSDP heuristics via `config/ssdp_rules.json`; mDNS TXT→summary and optional type rules via `config/mdns_rules.json`
- Device list + **icon grid**, category sidebar, manual reload
- Details dialogs (SSDP/mDNS payload builders with protocol metadata and services)
- Monitor / unfollow, user type/name/location overrides, icon appearance override (system/provided/custom with picker)
- Desktop notifications (connected / left) with optional **session notification history**
- Monitored devices restored as offline from prefs snapshots until rediscovered
- Single-instance behavior (one discovery stack per machine)

### MVP still open

- Continue field validation of **cross-protocol deduplication hardening** on diverse networks (edge cases around sleep/wake and endpoint changes)

### Future

- Additional protocols (NetBIOS, WSD, …), filter/search, copy IP, IPv6, packaging hardening

---

## Implementation constraints (MVP)

### 1) Cross-protocol deduplication

Same host may appear via SSDP and mDNS. The UI bundles rows with the same `(ip, port)`, and override persistence now prefers host identity (`UID`, then `MAC`, then IP fallback) to keep behavior stable across sources.

### 2) Thread-safe GTK updates

Discovery runs on background threads; UI updates use **`GLib.idle_add`** from `MainWindow`.

### 3) Presence and offline lifecycle

Offline must not flip on a single missed packet. SSDP uses announced **`max-age`** (doubled) plus NOTIFY byebye. mDNS uses host-level aggregation so one missing service does not instantly hide a still-present host.

### 4) URL and endpoint normalization

Centralize safe browser open; respect SSDP `presentationURL` when present.

### 5) Linux packaging and runtime dependencies

GTK + PyGObject from distro packages; `zeroconf` required for mDNS. Document prerequisites in README.

---

## Acceptance criteria (MVP) — status

| Area | Note |
|------|------|
| A Deduplication | Partial: SSDP stable keys + UI bundling; stronger cross-protocol merge still open |
| B GTK safety | Required pattern in place |
| C Lifecycle | SSDP + mDNS lifecycle implemented; tuning remains possible |
| D URLs | Best-effort from SSDP XML |
| E Packaging | Still to finalize |
| F Persistence | **`ui_prefs.json`** + monitored snapshots; safe if missing/corrupt prefs |

---

## Implementation roadmap (high level)

See **[`ROADMAP.md`](ROADMAP.md)** for ordered phases. Summary:

1. **Done:** foundation, SSDP, UI shell, prefs, monitored restore, notifications.
2. **Next:** runtime mode decision (app/service/tray/file manager/TBD), then packaging/release polish.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `pygobject` | GTK3 UI |
| `zeroconf` | mDNS discovery |
| stdlib `socket` | SSDP multicast |

---

## License

GNU Lesser General Public License v3 (LGPL v3)

## Author

Luc — ESP3D ecosystem
