# mDNS in NetNeighbor

## Protocol (short)

**mDNS** (multicast DNS / DNS-SD) advertises services on the local network, usually under
`*.local.` names. NetNeighbor uses `zeroconf` browsing callbacks; it does not perform an
active LAN scan.

Typical examples:

- `_http._tcp.local.` for web UIs
- `_esp3d._tcp.local.` for ESP3D services
- `_telnet._tcp.local.` for Telnet endpoints
- `_smb._tcp.local.` for SMB-capable hosts

## Code map

| Area | File / symbol | Role |
|------|----------------|------|
| Provider | `discovery/mdns.py` | `MDNSDiscovery` — zeroconf browser/listener, TXT decode, mapping, host aggregation. |
| Contract | `discovery/base.py` | `BaseDiscovery._emit("device", payload)` — normalized dict to manager. |
| Orchestration | `discovery/manager.py` | Receives `mdns` payloads, applies overrides, stores and notifies listeners. |
| Mapping hints | `data/device_types.json` | Optional per-service defaults (`type`, `icon`, `default_port`, `info_url`). Optionally extended by **`~/.config/netneighbor/device_types.json`**. |
| UI payload | `utils/details_payload.py` | `build_mdns_payload` — summary fields plus **per-service sections** (type, target, port, TXT pairs) for the expandable Services tab in details. |
| Rules | `config/mdns_rules.json` | User-extendible **TXT → summary line** mappings and optional `type_rules`; see [`MDNS_RULES_JSON.md`](MDNS_RULES_JSON.md). Loaded via `utils/mdns_rules.py`. |

## Browse strategy

- Service types are loaded from `data/device_types.json` (`mdns` section) and browsed immediately (labels / defaults from JSON).
- **DNS-SD service type enumeration** (`zeroconf.ZeroconfServiceTypes.find`, built on `_services._dns-sd._udp` queries) runs in a background thread so the UI is not blocked. Every type returned on the LAN is normalized to `…._tcp.local.` (or other transport) and gets its own `ServiceBrowser` if not already started.
- Enumeration repeats on a timer (every few minutes) and when `refresh()` is called, so devices that appear later or advertise uncommon types are still picked up without listing them all in JSON.
- Each add/update callback resolves `ServiceInfo`, decodes TXT, and emits a normalized payload.

## Host-level aggregation

mDNS announces services, but the UI must represent devices/hosts. NetNeighbor therefore:

1. tracks per-service payloads (`(service_type, instance_name)`),
2. maps each service to a host key (prefer IP, fallback hostname/server),
3. emits an **aggregated host payload** with all discovered services under `metadata["services"]`.

This is why a device like ESP3D can expose both `_esp3d._tcp` and `_telnet._tcp` and still appear
as one logical entry with multiple services in details.

## URL policy (important)

NetNeighbor builds a presentation URL for mDNS **only if `_http._tcp` is actually present** on
the host. It does **not** infer HTTP from a port number alone.

This avoids wrong browser links when non-HTTP services happen to use common ports.

## TXT records

- Per service instance, TXT is decoded from the raw `ServiceInfo.text` blob so **duplicate keys and all length-prefixed strings** are preserved in `metadata["services"][].txt_records` (ordered). A flat `metadata["txt"]` is still kept for heuristics (**last key wins**).

- After host-level aggregation, **`metadata["txt"]`** merges TXT from every service on that host for classification. The details dialog shows **one expandable row per discovered service**, with TXT nested under each row (`Gtk.Expander` via `DeviceDetailsDialog`).

## Type and name heuristics

- Type starts from `device_types.json` mapping, then context heuristics refine it.
- Current heuristics include practical cases such as:
  - `fluidnc` -> `cnc`
  - `laserjet` -> `networkprinter`
  - `synology` / `qnap` -> `nas`
- Display name prefers common TXT keys (`name`, `friendlyname`, `model`, ...), then service instance label.

## Online / offline lifecycle

- A host remains online as long as at least one tracked service for its host key remains.
- Removing one service updates the aggregated host payload; full offline is emitted when no services remain.

This avoids aggressive flapping when a multi-service host updates services independently.

## Debugging tips

- Enable `mdns` logs in `~/.config/netneighbor/logging.json` and inspect `discovery.mdns`.
- In details dialog (mDNS), verify:
  - TXT records
  - full `services` list
  - URL present only when `_http._tcp` exists.
