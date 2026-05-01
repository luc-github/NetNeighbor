# mDNS rules file (`config/mdns_rules.json`)

## Purpose

TXT records vary by vendor (`mfg`, `mdl`, custom keys). This file lets you **map TXT keys onto
summary lines** for the Device details **first tab** (without Python changes) and **extend type
classification** with substring rules similar to SSDP.

The app resolves `config/mdns_rules.json` at startup paths that load rules (`utils/mdns_rules.py`).
If the file is missing or invalid, built-in defaults are used and a log line is emitted.

**Repo path:** `config/mdns_rules.json` (ship with the project).  
Like SSDP, you can overlay rules from **`~/.config/netneighbor/mdns_rules.json`**
without touching install files — merged with bundled defaults; see [`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md).

## Relationship to code

| Area | Role |
|------|------|
| `utils/mdns_rules.py` | Loads JSON (cached once per process), TXT lookup helpers, optional `type_rules` evaluation |
| `utils/details_payload.py` | Adds `summary_from_txt` rows in `build_mdns_payload`; merge patch for SSDP + mDNS |
| `discovery/mdns.py` | Applies `type_rules` on the fallback path (after built-in FluidNC / LaserJet / NAS heuristics) |

## Schema (conceptual)

### `summary_from_txt`

Ordered list of objects; each emits **at most one** first-tab row when a value is found.

| Field | Type | Meaning |
|-------|------|--------|
| `label` | string | Detail column heading (use the same wording as SSDP when you want SSDP+mDNS merge, e.g. `Manufacturer`). |
| `keys` | list of strings | TXT key aliases, **priority order**. Match is **case-insensitive**. Lookup order: flattened host `metadata["txt"]`, then each `metadata["services"][].txt`. |

 Rows are suppressed when no key matches or the value is empty.

### `type_rules`

List evaluated **after** fixed heuristics in `MDNSDiscovery._infer_type_from_context` for the generic
path only (devices that already hit early returns stay unchanged).

| Field | Type | Meaning |
|-------|------|--------|
| `contains_any` | list of strings | Substrings (**lowercase**) searched inside the haystack (`service_key`, display name, `key=value` TXT pairs joined). First matching rule wins. |
| `type` | string | Internal type id (e.g. `nas`, `router`). Must align with `DiscoveryManager._category_for_type`. |

## How to extend

1. Add **`summary_from_txt`** entries with keys your devices actually advertise (check the Services / TXT dialog).
2. Reuse **`label`** spellings matching SSDP so combined SSDP+mDNS merges **fill in** weaker SSDP
   values (`unavailable`) from mDNS.
3. Add **`type_rules`** with specific `contains_any` groups **above** vague ones—first match wins.
4. Validate JSON: `python -m json.tool config/mdns_rules.json`

## What this file does *not* do

- It does not change browse targets (still `device_types.json` plus DNS‑SD enumeration).
- It does not alter host aggregation (`metadata["services"]`).
- Restart the app after editing JSON (rules are cached).

## See also

- [`MDNS_INTEGRATION.md`](MDNS_INTEGRATION.md) — mDNS pipeline overview.  
- [`SSDP_RULES_JSON.md`](SSDP_RULES_JSON.md) — parallel SSDP rules file.
