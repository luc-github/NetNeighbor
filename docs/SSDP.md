# SSDP in NetNeighbor

## Protocol overview

**SSDP** (Simple Service Discovery Protocol, part of UPnP) uses UDP multicast on `239.255.255.250:1900`.

| Message | Direction | Meaning |
|---------|-----------|---------|
| M-SEARCH | Client → multicast | "Who is there?" — devices respond with HTTP 200 |
| NOTIFY alive | Device → multicast | Unsolicited announcement |
| NOTIFY byebye | Device → multicast | Device going offline |

The announced **LOCATION** is an HTTP URL pointing to a device description XML file.
NetNeighbor fetches that document to get friendly name, device type, icons, presentation URL, etc.

NetNeighbor **listens** and **periodically sends M-SEARCH** — it does not scan the LAN.

## Code map

| Area | File / symbol | Role |
|------|---------------|------|
| Provider | `discovery/ssdp.py` | `SSDPDiscovery` — socket, listen thread, refresh thread, GC, XML fetch |
| Contract | `discovery/base.py` | `BaseDiscovery._emit("device", payload)` → normalized dict to manager |
| Orchestration | `discovery/manager.py` | `add_or_update_device` — overrides, SSDP merge, notify listeners |
| Model | `model/device.py` | `Device` — key prefers `UDN`, then `USN` base, then MAC, then `source:ip:port` |
| Rules | `config/ssdp_rules.json` | Naming / information / type rules |
| UI payload | `utils/details_payload.py` | `build_ssdp_payload` — rows for the details dialog |

## From packet to Device

1. Parse headers (`LOCATION`, `ST`/`NT`, `USN`, `SERVER`, `CACHE-CONTROL`, …)
2. Derive `ip`/`port` from `LOCATION` URL (default 80)
3. Fetch and parse XML from `LOCATION` (cached briefly)
4. Infer `name` and `type` from headers + XML; apply `ssdp_rules.json`
5. Emit payload with `metadata` holding headers, `xml_fields`, raw XML

## Online / offline

- **Online**: every valid response or NOTIFY alive refreshes the seen timestamp
- **NOTIFY byebye**: immediate `online: false` payload
- **Timeout**: GC emits `online: false` when expiry passes.  
  Expiry ≈ `2 × CACHE-CONTROL max-age`, clamped to a maximum; default minimum when not specified.

## Merge (same physical device)

`DiscoveryManager` may merge SSDP updates sharing the same `ip:port` (MAC-assisted) and merges XML metadata so the UI shows one logical row per stable `Device.key`.

---

## Rules file (`config/ssdp_rules.json`)

Lets you **tune naming, information text, and type classification** without Python changes.
User overlay: `~/.config/netneighbor/ssdp_rules.json` — merged with bundled defaults.
See [`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md).

### `name_rules`

| Field | Type | Meaning |
|-------|------|---------|
| `fallback_fields` | list | Ordered `xml_fields` keys to use as name when earlier keys are empty |
| `prefer_display_name_when_no_friendly_name` | bool | Use `displayName` if `friendlyName` is missing |

### `information_rules`

| Field | Type | Meaning |
|-------|------|---------|
| `concat_fields` | list | XML field names to join for the "Information" detail row |
| `separator` | string | Placed between non-empty values |

### `type_rules`

Evaluated in order; first match wins.

| Field | Type | Meaning |
|-------|------|---------|
| `contains_any` | list | Lowercase substrings searched in SSDP headers + selected XML fields |
| `type` | string | Internal type id (e.g. `router`, `nas`, `smarttv`) |

### How to extend

1. Put specific `contains_any` groups **before** generic ones.
2. Use tokens that appear in your network's actual SSDP/XML (check SSDP details dialog).
3. Keep `type` values consistent with `data/device_types.json`.
4. Validate: `python -m json.tool config/ssdp_rules.json`
5. Restart the app.

## Debugging

- Enable `ssdp` in `~/.config/netneighbor/logging.json`
- SSDP details dialog: inspect LOCATION, USN, XML presence
- Watch for "XML fetched" vs fetch failures (timeout, parse error)

## See also

- [`MDNS.md`](MDNS.md) — parallel mDNS pipeline
- [`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md) — user overlay rules
- [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md) — discovery manager overview
