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
User overlay: `~/.config/netneighbor/ssdp_rules.json` — merged with the bundled file.
See [`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md).

> **Single source of truth.** This bundled file *is* the rules — there is no Python copy. The
> startup integrity check (`utils/config_integrity.py`) refuses to launch with a "corrupted
> installation, please reinstall" dialog if it is missing or not valid JSON.

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

SSDP type classification is **fully data-driven** — there is no hardcoded type chain in
`ssdp.py` anymore. Two ordered lists (both first-match-wins), evaluated by `_match_type_rules`:

- **`type_rules`** — curated, specific rules. The **override** stage (`_apply_type_rules`),
  evaluated against a *rich* haystack (SSDP headers **plus** `friendlyName` / `displayName` /
  `roomName` / `modelName` …). Tokens here must be specific because they see free-form names.
- **`base_type_rules`** — coarse fallbacks formerly hardcoded in `_infer_type` (`mediaserver`,
  `router`, `printer`, `computer`, `modelType==nas`, …). The **base** stage (`_infer_type`),
  evaluated against a *narrow* haystack (ST/USN/SERVER/modelName/manufacturer only — **no**
  friendly/room names) so loose tokens like `wan` don't false-positive on a device named
  e.g. *"Rowan's iPhone"*. These fire only when no `type_rules` entry matched.

| Field | Type | Meaning |
|-------|------|---------|
| `contains_any` | list | Lowercase substrings searched in the stage's haystack |
| `equals_field` | object | `{fieldName: [values]}` — exact, case-insensitive match against a field (e.g. `{"modelType": ["nas"]}`) |
| `type` | string | Internal type id (e.g. `router`, `nas`, `smarttv`) |

A rule matches if **either** `contains_any` or `equals_field` is satisfied. Use `equals_field`
when a substring match would be too loose (a NAS that also advertises DLNA must stay `nas`).

### How to extend

1. Add device-specific rules to **`type_rules`** (specific tokens only); leave `base_type_rules`
   for coarse fallbacks. Put more specific `contains_any` groups before generic ones.
2. Use tokens that appear in your network's actual SSDP/XML (check SSDP details dialog).
3. Keep `type` values consistent with `config/device_types.json`.
4. Validate: `python -m json.tool config/ssdp_rules.json`
5. Restart the app.

> User overlay (`~/.config/netneighbor/ssdp_rules.json`) prepends to **`type_rules`** — the
> right place for your own classifications. `base_type_rules` stays as shipped.

## Debugging

- Enable `ssdp` in `~/.config/netneighbor/logging.json`
- SSDP details dialog: inspect LOCATION, USN, XML presence
- Watch for "XML fetched" vs fetch failures (timeout, parse error)

## See also

- [`MDNS.md`](MDNS.md) — parallel mDNS pipeline
- [`WSD.md`](WSD.md) — WS-Discovery pipeline
- [`NETBIOS.md`](NETBIOS.md) — NetBIOS pipeline
- [`COMMUNITY_OVERRIDES.md`](../contributing/COMMUNITY_OVERRIDES.md) — user overlay rules
- [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md) — discovery manager overview
