# Backend architecture

## Overview

The backend is built around a single `DiscoveryManager` instance that owns the in-memory
device store and all user overrides. Protocol modules run in their own threads and emit
normalized payloads; the manager applies overrides and notifies UI listeners via `GLib.idle_add`.

```
┌────────────────────────────────────────────────────────┐
│  Protocol threads                                      │
│  SSDPDiscovery  MDNSDiscovery  WSDDiscovery  NetBIOS   │
│       │              │              │           │      │
│       └──────────────┴──────────────┴───────────┘      │
│                           │ _emit("device", payload)   │
└───────────────────────────┼────────────────────────────┘
                            ▼
               ┌────────────────────────┐
               │   DiscoveryManager     │
               │  add_or_update_device  │
               │  apply overrides       │
               │  merge protocols       │
               │  _notify(devices)      │
               └────────────┬───────────┘
                            │ listener(devices)  [GLib.idle_add]
                            ▼
               ┌────────────────────────┐
               │   MainWindow / UI      │
               └────────────────────────┘
```

---

## DiscoveryManager (`discovery/manager.py`)

Central coordinator. All state is protected by a single `threading.Lock`.

### Device store

```python
self._devices: dict[str, Device]  # key → Device
```

The key is derived from the device identity (`Device.key`): `name:source` when a stable name
is available, otherwise `source:ip:port`. This key determines how updates from multiple
protocols are stored or merged.

### Override system

User preferences are stored as dictionaries keyed by device identity and applied to each
`Device` when it is added or updated. Overrides live in `ui_prefs.json`.

| Override | Store type | Applied via | Metadata key set |
|----------|-----------|-------------|-----------------|
| Name | `dict[str, str]` | `_apply_name_override` | `device.name` |
| Type | `dict[str, str]` | `_apply_type_override` | `device.type` |
| Location | `dict[str, str]` | `_apply_location_override` | `device.location` |
| URL (legacy) | `dict[str, str]` | `_apply_url_override` | `metadata["url_override"]` |
| Device commands | `dict[str, list[dict]]` | `_apply_device_commands` | `metadata["device_commands"]` |
| Custom command | `dict[str, str]` | `_apply_custom_command_override` | `metadata["custom_command"]` |
| Monitored | `dict[str, bool]` | `_apply_monitored_override` | `device.monitored` |
| Field mapping rules | `dict[str, dict]` | `_apply_field_mapping_rules` | `metadata["field_rules"]` |

Override lookup uses `_find_override_value(store, device)` which tries the name-based key
first, then falls back to `source:ip:port`.

### Device commands

Per-device connection commands (set in the device's **Options** tab). A list of entries:

```python
[
    {"scheme": "http", "ip": "", "port": 8080, "mode": "override", "label": ""},
    {"scheme": "ssh",  "ip": "192.168.1.10", "port": 22, "mode": "additional", "label": "Admin"},
]
```

- **override**: replaces the auto-detected default for that scheme on double-click
- **additional**: adds an extra entry in the Open submenu without replacing the default
- Empty `ip` → use device's discovered IP
- Empty/0 `port` → use scheme default port

Validated and normalized by `_normalize_command_list()`.

### Pipeline (add/update)

Every incoming payload runs through the full pipeline in `_add_or_update_impl`:

1. Build or update the `Device` object
2. Apply all overrides (name, type, location, url, device_commands, custom_command, monitored, field rules)
3. Merge with existing device if same key
4. Update `_devices` store
5. Call `_notify()` → each registered listener receives the full device list

Three call sites trigger the pipeline: direct device emission, SSDP prefetch completion,
and the WSD/mDNS aggregation path.

### Presence transitions

Optional hooks registered via `register_presence_transition_hook`: called on
`"online"` / `"offline"` transitions. Callbacks run on the discovery thread —
use `GLib.idle_add` before touching GTK.

---

## Protocol modules

### SSDP (`discovery/ssdp.py`)

- Listens on UDP 1900 (multicast `239.255.255.250`)
- Sends periodic M-SEARCH (multiple `ST` values)
- Fetches and parses XML from LOCATION headers
- Offline: NOTIFY byebye (immediate) or `CACHE-CONTROL max-age` expiry (GC)
- See [`SSDP.md`](SSDP.md) for full details

### mDNS (`discovery/mdns.py`)

- Uses `zeroconf` library (passive browsing + DNS-SD enumeration)
- Aggregates per-service payloads into per-host entries
- URL only set when `_http._tcp` is actually advertised
- **Grace period on removal**: `remove_service` callbacks are delayed 180 s when the host is still alive, absorbing mDNS TTL jitter that would otherwise cause false offline flaps
- **Refresh = full Zeroconf restart**: `refresh()` closes and reopens the `Zeroconf` instance to clear its DNS cache, then recreates all `ServiceBrowser` objects. This ensures PTR queries go out without *known-answer suppression* headers (RFC 6762 §7.1), so devices that stopped announcing (TTL expired) will respond and repopulate the device list
- See [`MDNS.md`](MDNS.md) for full details

### WS-Discovery (`discovery/wsd.py`, `discovery/wsdd_client.py`)

- Implements WSD (WS-Discovery) on UDP 3702 for Windows-compatible hosts and printers
- Uses PyPI `WSDiscovery` (optional import; graceful degradation if absent)
- Also reads `wsdd` daemon cache when the `wsdd` system service is running

### NetBIOS (`discovery/netbios.py`)

- Calls `nmblookup -A <ip>` (Samba client tool) to resolve NetBIOS names
- Directed probes run in parallel via `ThreadPoolExecutor(max_workers=8)`
- Supplementary: adds name/workgroup context to already-discovered SSDP/WSD hosts
- Requires `samba-client` or `samba-common-bin` to be installed

---

## Data model (`model/device.py`)

```python
@dataclass
class Device:
    source: str          # "ssdp" | "mdns" | "wsd" | "netbios" | …
    ip: str
    port: int
    name: str
    type: str            # "computer" | "nas" | "router" | "printer" | …
    category: str        # derived from type
    url: str | None      # presentation URL (HTTP/HTTPS)
    location: str | None # room / location label
    online: bool
    monitored: bool
    metadata: dict       # protocol-specific data + override results
    key: str             # stable identity key
```

The `key` is what the manager uses for override lookup and deduplication across protocols.

---

## Cross-protocol merge

When two protocols discover the same physical device (e.g. a NAS seen via both SSDP and mDNS):

- They are stored under **separate keys** in `_devices` (different sources)
- `DeviceList` in the UI **bundles** entries that share `(ip, port)` into one `_DeviceBundle`
- The bundle's **primary** device follows `merge.information_precedence` in `discovery.json`
  (default: `user_override > ssdp_live > ssdp_profile_cache > mdns`)
- MAC address from the ARP/neighbor cache (`utils/neighbor_mac.py`) assists bundle merge
  when different protocols report slightly different IPs for the same host

### SSDP profile cache hydration

When an mDNS or live SSDP device arrives, the manager pre-populates its `metadata` from the persisted SSDP profile cache (`~/.cache/netneighbor/discovery-cache.json`) **before** the live XML fetch completes:

- `_hydrate_mdns_from_ssdp_profile_cache` — copies `xml_fields`, `raw_xml`, and `ssdp_location` into mDNS devices; enables the "Device data" tab and rich fields (Manufacturer, Model…) even when no SSDP device is alive
- `_hydrate_ssdp_from_profile_cache` — same for live SSDP devices whose XML fetch is still pending or has failed

The live XML fetch result takes precedence and overwrites cached values once it arrives.

### Loopback filtering

All devices with a loopback IP address (`ipaddress.ip_address(ip).is_loopback`) are discarded unconditionally in `add_or_update_device`. This covers `127.0.0.1`, `127.x.x.x`, `::1` and any other loopback — they are always the local machine and have no value as network neighbours.

---

## Config files

| File | Purpose |
|------|---------|
| `config/ssdp_rules.json` | SSDP name / info / type classification rules |
| `config/mdns_rules.json` | mDNS TXT → summary mapping + type rules |
| `config/default_commands.json` | Default command templates per URL scheme (used by Tools → External applications) |
| `data/device_types.json` | Icon, display name, and mDNS service type → device type mapping |
| `~/.config/netneighbor/discovery.json` | Discovery toggles, intervals, merge order |
| `~/.config/netneighbor/ui_prefs.json` | All user overrides + UI state |

---

## Thread safety

- `DiscoveryManager` uses a single `threading.RLock` around all state mutations
- UI updates are marshaled to the GTK main loop via `GLib.idle_add`
- Protocol threads must never call GTK directly

---

## See also

- [`UI_ARCHITECTURE.md`](UI_ARCHITECTURE.md) — GTK frontend
- [`SSDP.md`](SSDP.md) — SSDP protocol detail
- [`MDNS.md`](MDNS.md) — mDNS protocol detail
- [`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md) — user config overlays
- [`MAINTENANCE.md`](MAINTENANCE.md) — config paths, logging, debugging
