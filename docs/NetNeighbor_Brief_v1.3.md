# NetNeighbor — Project Brief v1.3

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
- Distributable as AppImage / .deb

---

## Scope

### In scope (MVP)
- SSDP (UPnP multicast) discovery
- mDNS (zeroconf/Avahi) discovery
- PC detection via WSD/SSDP (`urn:schemas-microsoft-com:device:Computer:1`)
- PC detection via `_smb._tcp` mDNS if Avahi active

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

## Architecture

```
netneighbor/
├── main.py                  # Entry point
├── app.py                   # GTK Application class
│
├── discovery/
│   ├── __init__.py
│   ├── base.py              # BaseDiscovery abstract class (protocol API)
│   ├── manager.py           # Discovery orchestrator
│   ├── ssdp.py              # SSDP multicast discovery
│   └── mdns.py              # mDNS discovery via zeroconf
│
├── model/
│   ├── __init__.py
│   └── device.py            # Device data class
│
├── ui/
│   ├── __init__.py
│   ├── main_window.py       # Main GTK window
│   ├── device_list.py       # GTK TreeView with category sections
│   ├── device_details.py    # Details dialog (XML / TXT records)
│   └── icons.py             # Icon resolver
│
├── assets/
│   └── icons/               # Self-contained icon set (PNG + SVG)
│       ├── esp32.png
│       ├── printer.png
│       ├── nas.png
│       ├── router.png
│       ├── mediaserver.png
│       ├── computer.png
│       ├── http.png
│       └── unknown.png
│
├── utils/
│   ├── __init__.py
│   └── browser.py           # Open URL in default browser
│
├── data/
│   └── device_types.json    # Static mapping: UPnP deviceType / mDNS service → icon + label + category
│
├── config/
│   └── (~/.config/netneighbor/)
│       └── devices.json     # Lightweight local device history
│
├── docs/
│   └── CONTRIBUTING_ICONS.md  # Icon specs + how to register in device_types.json
│
├── requirements.txt
├── README.md
└── LICENSE
```

---

## Discovery API

```python
# discovery/base.py
class BaseDiscovery:
    """Base class for all discovery protocols.
    New protocols must inherit this class and implement all methods.
    Register in DiscoveryManager to activate."""

    def start(self) -> None:
        """Start discovery listener"""
        raise NotImplementedError

    def stop(self) -> None:
        """Stop discovery listener"""
        raise NotImplementedError

    def refresh(self) -> None:
        """Trigger active discovery query"""
        raise NotImplementedError

    def set_callback(self, callback: callable) -> None:
        """Register callback for device found/updated/lost events"""
        raise NotImplementedError
```

```python
# discovery/manager.py
class DiscoveryManager:
    def __init__(self):
        self._protocols: list[BaseDiscovery] = [
            SSDPDiscovery(),
            mDNSDiscovery(),
            # Future: WSDDiscovery(), NetBIOSDiscovery()...
        ]
```

---

## Data Flow

```
DiscoveryManager
    ├── SSDPDiscovery  ──→ Device objects
    └── mDNSDiscovery  ──→ Device objects
            │
            ▼
     DeviceStore (in-memory list)
            │
            ├──→ devices.json (~/.config/netneighbor/)
            │
            ▼
      GTK TreeView (categories)  ──→  MainWindow
```

---

## Device Model

```python
Device:
    name: str            # Human-readable name
    ip: str              # IP address
    port: int            # Primary service port
    type: str            # Device type id (ex: "esp32", "printer")
    category: str        # UI category (ex: "ESP3D Devices")
    source: str          # Discovery protocol: "ssdp" | "mdns"
    url: str             # URL to open in browser
    metadata: dict       # raw XML (SSDP) or TXT records (mDNS)
    last_seen: datetime
    online: bool         # False = greyed out in UI
    icon: str | None     # Icon filename, resolved by icons.py
```

---

## Device Categories (UI)

```
▼ Computers          (WSD/SSDP, _smb._tcp, _afpovertcp._tcp)
▼ ESP3D Devices      (_esp3d._tcp, _arduino._tcp)
▼ Routers & Gateways (UPnP WANDevice)
▼ Media Servers      (UPnP MediaServer, _daap._tcp)
▼ Printers           (UPnP Printer, _printer._tcp, _ipp._tcp)
▼ NAS / File Servers (Synology, QNAP, _nfs._tcp)
▼ Unknown Devices    (everything else)
```

---

## Device Types Database (`data/device_types.json`)

```json
{
  "ssdp": {
    "urn:schemas-upnp-org:device:MediaServer:1": {
      "label": "Media Server",
      "category": "Media Servers",
      "icon": "mediaserver.png",
      "default_port": 80,
      "info_url": "http://{ip}:{port}/"
    },
    "urn:schemas-upnp-org:device:WANDevice:1": {
      "label": "Router / Gateway",
      "category": "Routers & Gateways",
      "icon": "router.png",
      "default_port": 80,
      "info_url": "http://{ip}:{port}/"
    },
    "urn:schemas-upnp-org:device:Printer:1": {
      "label": "Printer",
      "category": "Printers",
      "icon": "printer.png",
      "default_port": 80,
      "info_url": null
    },
    "urn:schemas-microsoft-com:device:Computer:1": {
      "label": "Computer",
      "category": "Computers",
      "icon": "computer.png",
      "default_port": 80,
      "info_url": null
    }
  },
  "mdns": {
    "_esp3d._tcp": {
      "label": "ESP3D Device",
      "category": "ESP3D Devices",
      "icon": "esp32.png",
      "default_port": 80,
      "info_url": "http://{ip}:{port}/"
    },
    "_smb._tcp": {
      "label": "Computer",
      "category": "Computers",
      "icon": "computer.png",
      "default_port": 445,
      "info_url": null
    },
    "_printer._tcp": {
      "label": "Printer",
      "category": "Printers",
      "icon": "printer.png",
      "default_port": 631,
      "info_url": null
    },
    "_ipp._tcp": {
      "label": "Printer (IPP)",
      "category": "Printers",
      "icon": "printer.png",
      "default_port": 631,
      "info_url": null
    },
    "_afpovertcp._tcp": {
      "label": "Mac (File Sharing)",
      "category": "Computers",
      "icon": "computer.png",
      "default_port": 548,
      "info_url": null
    },
    "_daap._tcp": {
      "label": "Media Server (DAAP)",
      "category": "Media Servers",
      "icon": "mediaserver.png",
      "default_port": 3689,
      "info_url": null
    },
    "_nfs._tcp": {
      "label": "NAS / File Server",
      "category": "NAS / File Servers",
      "icon": "nas.png",
      "default_port": 2049,
      "info_url": null
    },
    "_http._tcp": {
      "label": "HTTP Device",
      "category": "Unknown Devices",
      "icon": "http.png",
      "default_port": 80,
      "info_url": "http://{ip}:{port}/"
    }
  },
  "fallback": {
    "label": "Unknown Device",
    "category": "Unknown Devices",
    "icon": "unknown.png",
    "default_port": 80,
    "info_url": null
  }
}
```

---

## Icon Resolution Priority

```
1. Icon provided in SSDP XML
2. Icon from assets/icons/ via device_types.json
3. assets/icons/unknown.png (final fallback)
```

## Icon Specs

- **Format**: PNG (required) + SVG (recommended)
- **Size**: 64x64px minimum, 256x256px recommended
- **Background**: transparent
- **Naming**: `{type_id}.png` (ex: `esp32.png`)
- **Self-contained**: no system icon theme dependency
- See `docs/CONTRIBUTING_ICONS.md` for contribution guidelines

---

## Features

### MVP
- SSDP multicast discovery
- mDNS discovery via zeroconf
- Device list with category sections (GTK TreeView)
- Icon per device type
- Manual Refresh button
- Double-click → opens device URL in browser
- Right-click menu → Details (SSDP XML / mDNS TXT records)
- Offline devices shown greyed out (from devices.json history)

### Future
- Additional protocol support (NetBIOS, WSD, etc.)
- Filter / search bar
- Copy IP to clipboard
- Desktop notifications for new devices
- Icon grid view
- IPv6 support

---

## Implementation Constraints (MVP)

To keep the first release reliable across Linux desktop environments, the following technical
constraints are part of the MVP scope.

### 1) Cross-protocol deduplication
- The same physical device may be discovered by both SSDP and mDNS.
- `DeviceStore` must merge entries that represent the same host instead of showing duplicates.
- A deterministic merge strategy is required:
  - Prefer stable identifiers when available (USN/UUID, hostname/FQDN, serial-like TXT fields)
  - Fallback to heuristic matching (`ip + normalized_name + type`)
- Store all contributing sources in metadata (`sources: ["ssdp", "mdns"]`) for traceability.

### 2) Thread-safe GTK updates
- Discovery listeners may run outside the GTK main loop.
- UI updates must only happen on the GTK thread.
- All device add/update/remove signals routed to UI must be marshaled through `GLib.idle_add(...)`.

### 3) Presence and offline lifecycle
- "Offline" state must not depend on instantaneous packet loss.
- Each device keeps `last_seen` and protocol-specific timeout/TTL information.
- A periodic lifecycle check marks devices offline only after timeout expiration.
- A device returning on the network must be transitioned back to online cleanly (same logical entry).

### 4) URL and endpoint normalization
- Announced service endpoints may be incomplete or inconsistent.
- URL construction must be centralized in one resolver utility:
  - Respect protocol metadata when valid
  - Fallback to mapping defaults from `device_types.json`
  - Never generate malformed URLs
- Resolver output is the single source used by double-click "Open in browser".

### 5) Linux packaging and runtime dependencies
- Packaging targets remain AppImage and `.deb`, but system prerequisites must be explicit.
- Runtime validation must cover at least:
  - `python3-gi` / GTK3 availability
  - `zeroconf` Python dependency
  - mDNS backend expectations on target distros (Avahi/DBus ecosystem)
- README and release notes must include a dependency troubleshooting section for Mint/Ubuntu/Debian.

---

## Acceptance Criteria (MVP)

The MVP is considered valid only if all criteria below are met on at least Linux Mint, Ubuntu,
or Debian test environments.

### A) Discovery and deduplication
- Given one device announcing both via SSDP and mDNS, only one logical entry appears in the UI.
- The merged entry retains traceability of protocol origins in metadata.
- Repeated announcements do not create duplicate rows.

### B) UI thread safety and responsiveness
- No GTK thread warnings/crashes occur during continuous discovery traffic.
- Device list updates remain fluid while listeners are active.
- All add/update/remove UI operations are executed through GTK main-loop-safe dispatch.

### C) Online/offline lifecycle behavior
- A discovered device is marked offline only after timeout expiration, not after a single missed packet.
- If the same device reappears, its existing entry is reactivated (not duplicated).
- `last_seen` is updated on every valid re-announcement.

### D) URL resolution reliability
- Double-click action opens a valid browser URL when an endpoint is available.
- If announced endpoint fields are incomplete, fallback mapping from `device_types.json` is applied.
- No malformed URL is emitted by the resolver for known mappings.

### E) Packaging and runtime readiness
- Application starts successfully on target distro with documented dependencies installed.
- Missing dependency scenarios produce understandable user-facing diagnostics (or actionable logs).
- Build and run steps for AppImage and `.deb` are documented and reproducible from repository docs.

### F) Regression and data persistence checks
- `devices.json` persists history across app restarts.
- Previously seen devices can be displayed as offline (greyed out) until rediscovered.
- Corrupted or missing `devices.json` does not crash the application (safe fallback to empty state).

---

## Implementation Roadmap

### Phase 0 — Foundation (project bootstrap)
- Create package structure (`discovery`, `model`, `ui`, `utils`, `data`).
- Add app entrypoint and GTK application/window wiring.
- Add typed `Device` model and in-memory store contract.
- Deliverable: app launches with empty UI and manual refresh action.

### Phase 1 — mDNS integration
- Implement `mDNSDiscovery` using `zeroconf` service browser.
- Normalize mDNS records into `Device` objects.
- Wire callbacks to manager and thread-safe UI updates via `GLib.idle_add(...)`.
- Deliverable: `_esp3d._tcp`, `_smb._tcp`, `_http._tcp` devices appear in UI.

### Phase 2 — SSDP integration
- Implement `SSDPDiscovery` multicast listener/query over UDP.
- Parse SSDP headers and resolve device descriptor metadata.
- Normalize SSDP records into `Device` objects and feed manager callbacks.
- Deliverable: UPnP devices appear in UI and can be opened in browser if URL exists.

### Phase 3 — Merge, lifecycle, and persistence
- Add cross-protocol deduplication logic in store/manager.
- Implement offline lifecycle checks (`last_seen`, timeout scheduler).
- Persist lightweight history to `~/.config/netneighbor/devices.json`.
- Deliverable: no duplicate rows for same host, offline state visible after timeout.

### Phase 4 — UX hardening and diagnostics
- Add details dialog for raw metadata (XML/TXT).
- Add robust URL resolver with mapping fallback.
- Improve error handling for missing dependencies and parsing failures.
- Deliverable: stable daily-use MVP with actionable diagnostics.

### Phase 5 — Packaging and release
- Add Linux install/run docs for Mint/Ubuntu/Debian.
- Prepare reproducible AppImage and `.deb` build process.
- Validate acceptance criteria on target distros.
- Deliverable: tagged MVP release candidate ready for user validation.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `pygobject` | GTK3 UI |
| `zeroconf` | mDNS discovery |
| stdlib `socket` | SSDP multicast (no extra dep) |

---

## License
GNU Lesser General Public License v3 (LGPL v3)

## Author
Luc — ESP3D ecosystem
