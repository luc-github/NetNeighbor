# mDNS in NetNeighbor

## Protocol overview

**mDNS** (multicast DNS / DNS-SD) advertises services on the local network under `*.local.` names.
NetNeighbor uses `zeroconf` browsing callbacks; it does not perform an active LAN scan.

Typical service types discovered:

| Type | Meaning |
|------|---------|
| `_http._tcp` | Web UI |
| `_smb._tcp`, `_microsoft-ds._tcp` | File sharing |
| `_ssh._tcp` | SSH |
| `_ftp._tcp` | FTP |
| `_telnet._tcp` | Telnet |
| `_esp3d._tcp` | ESP3D firmware |

## Code map

| Area | File / symbol | Role |
|------|---------------|------|
| Provider | `discovery/mdns.py` | `MDNSDiscovery` — zeroconf browser/listener, TXT decode, host aggregation |
| Contract | `discovery/base.py` | `BaseDiscovery._emit("device", payload)` → normalized dict to manager |
| Orchestration | `discovery/manager.py` | Receives `mdns` payloads, applies overrides, stores and notifies |
| Mapping hints | `config/device_types.json` | Per-service defaults (`type`, `icon`, `default_port`) |
| UI payload | `utils/details_payload.py` | `build_mdns_payload` — per-service sections for the Services tab |
| Rules | `config/mdns_rules.json` | TXT → summary line mappings and optional `type_rules` |

## Browse strategy

- Service types from `config/device_types.json` (`mdns` section) are browsed immediately.
- **DNS-SD enumeration** (`_services._dns-sd._udp`) runs in background to catch unlisted types.
- Enumeration repeats on a timer and on `refresh()` — no types need to be listed statically.

## Host-level aggregation

mDNS announces per-service. NetNeighbor aggregates services to one logical host entry:

1. Track per-service payloads `(service_type, instance_name)`
2. Map each service to a host key (prefer IP, fallback hostname)
3. Emit an aggregated payload with all services under `metadata["services"]`

A device like ESP3D exposing both `_esp3d._tcp` and `_telnet._tcp` appears as one entry
with both services visible in the Details dialog.

## URL policy

A presentation URL is built **only if `_http._tcp` is actually present**. Port numbers alone do not imply HTTP — this avoids wrong browser links for non-HTTP services on common ports.

## TXT records

- Raw `ServiceInfo.text` is decoded preserving duplicate keys and ordering → `metadata["services"][].txt_records`
- A flat `metadata["txt"]` (last-key-wins) is kept for type heuristics
- Details dialog shows one expandable row per service with TXT pairs nested under it

## Type heuristics

Type starts from `device_types.json`, refined by heuristics (`fluidnc→cnc`, `synology/qnap→nas`, etc.), then `type_rules` in `mdns_rules.json`.

## Online / offline lifecycle

A host stays online while at least one tracked service remains. Removing one service updates the aggregated payload; offline is emitted only when all services are gone — avoids flapping for multi-service hosts.

When the last service disappears while the host is still otherwise alive, the removal is delayed by a **120 s grace period** to absorb mDNS TTL jitter. A user-initiated `refresh()` reschedules any pending grace-removes to a short **15 s window**, so a device that truly left the network disappears quickly instead of lingering for the full grace period.

Brutal disconnections (power loss, cable pull) emit no byebye and would otherwise persist until the mDNS TTL expires. The manager's periodic ICMP probe (see [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md#reachability-probe--offline-removal)) flips such hosts offline within ~180 s and drops non-monitored ones from the list.

---

## Rules file (`config/mdns_rules.json`)

Lets you **map TXT keys onto summary lines** and **extend type classification** without Python changes.
User overlay: `~/.config/netneighbor/mdns_rules.json` — merged with bundled defaults.
See [`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md).

### `summary_from_txt`

Ordered list; each entry emits at most one first-tab detail row.

| Field | Type | Meaning |
|-------|------|---------|
| `label` | string | Detail column heading (match SSDP wording to enable cross-protocol merge) |
| `keys` | list | TXT key aliases, priority order, case-insensitive |

### `type_rules`

Evaluated after fixed heuristics. First match wins.

| Field | Type | Meaning |
|-------|------|---------|
| `contains_any` | list | Lowercase substrings searched in service key, name, and TXT pairs |
| `type` | string | Internal type id (e.g. `nas`, `router`) |

### How to extend

1. Add `summary_from_txt` entries matching your devices' actual TXT keys (check Services tab in Details).
2. Use the same `label` as SSDP counterparts so SSDP+mDNS detail rows merge correctly.
3. Add `type_rules` with specific `contains_any` before generic ones.
4. Validate: `python -m json.tool config/mdns_rules.json`
5. Restart the app (rules are cached at startup).

## Debugging

- Enable `mdns` in `~/.config/netneighbor/logging.json`
- Details dialog → Services tab: verify TXT records, services list, URL present only when `_http._tcp` exists

## See also

- [`SSDP.md`](SSDP.md) — parallel SSDP pipeline
- [`WSD.md`](WSD.md) — WS-Discovery pipeline
- [`NETBIOS.md`](NETBIOS.md) — NetBIOS pipeline
- [`COMMUNITY_OVERRIDES.md`](../contributing/COMMUNITY_OVERRIDES.md) — user overlay rules
- [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md) — discovery manager overview
