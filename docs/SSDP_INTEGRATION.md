# SSDP in NetNeighbor

## Protocol (short)

**SSDP** (Simple Service Discovery Protocol) is part of UPnP. Devices and services send
**UDP** messages on the IPv4 multicast address `239.255.255.250:1900`. Typical message types:

- **M-SEARCH** — client request (“search for service type X”); devices may respond with `HTTP/1.1 200`.
- **NOTIFY** — unsolicited announcement (`ssdp:alive`, `ssdp:byebye`).

Announced **LOCATION** is an HTTP URL of a **device description** (XML). The app fetches
that document to obtain friendly name, device type, icons, presentation URL, etc.

NetNeighbor **does not scan** the LAN; it **listens** and **periodically sends M-SEARCH**
queries defined in `SSDPDiscovery.refresh()`.

## Code map

| Area | File / symbol | Role |
|------|----------------|------|
| Provider | `discovery/ssdp.py` | `SSDPDiscovery` — socket, listen thread, refresh thread, garbage collection, XML fetch. |
| Contract | `discovery/base.py` | `BaseDiscovery._emit("device", payload)` — normalized dict to manager. |
| Orchestration | `discovery/manager.py` | `DiscoveryManager.add_or_update_device` — overrides, SSDP merge, `Device.key`, notify listeners. |
| Model | `model/device.py` | `Device` — SSDP keys prefer `UDN`, then `USN` base, then MAC, then `source:ip:port`. |
| Rules | `config/ssdp_rules.json` | Optional naming / information / type rules (see `SSDP_RULES_JSON.md`). |
| UI payload | `utils/details_payload.py` | `build_ssdp_payload` — rows for the details dialog. |

## Listen and query

- Socket binds to UDP port **1900** when possible; otherwise ephemeral port (fallback).
- Joins multicast group when the OS allows.
- **Refresh** sends M-SEARCH for several `ST` values (`ssdp:all`, `upnp:rootdevice`, DIAL, …).
- A **refresh interval** thread repeats M-SEARCH periodically.

## From packet to `Device`

1. Parse headers (`LOCATION`, `ST`/`NT`, `USN`, `SERVER`, `CACHE-CONTROL`, …).
2. Derive `ip` / `port` from `LOCATION` URL (default port 80).
3. Fetch and parse XML from `LOCATION` (cached briefly per URL).
4. Infer `name` and `type` from headers + XML; apply `ssdp_rules.json`.
5. Emit payload with `metadata` holding headers, `xml_fields`, raw `xml` string, etc.

## Online / offline

- **Online**: every valid response / NOTIFY alive refreshes the internal seen timestamp.
- **NOTIFY** `ssdp:byebye`: emits a device payload with `online: false` (immediate).
- **Timeout**: if no refresh before expiry, GC emits `online: false`.  
  Expiry is derived from **`CACHE-CONTROL: max-age=…`** when present: timeout ≈ **`2 × max-age`** seconds, clamped to a maximum; otherwise a default minimum applies. This reduces flapping when devices sleep or announce slowly.

## Merge behavior (same physical device)

`DiscoveryManager` may merge SSDP updates that share the same endpoint (`ip:port`) when
MAC constraints allow, and merges XML metadata (including alternate names) so the UI keeps
a single logical row per stable `Device.key`.

## Debugging tips

- Enable logging (see `MAINTENANCE.md`) and watch `discovery.ssdp` for RX/TX and XML fetch failures.
- Use the UI **SSDP details** dialog to inspect raw metadata for a device.
