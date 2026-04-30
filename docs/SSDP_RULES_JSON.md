# SSDP rules file (`config/ssdp_rules.json`)

## Purpose

SSDP devices expose a lot of variation: multiple XML profiles for one host, generic
`ST` / `USN` strings, and marketing names in different fields. The rules file lets you
**tune naming, short “information” text, and device type classification** from JSON
without changing Python code.

The app loads this file at SSDP discovery startup. If the file is missing or invalid,
built-in defaults are used and a log message is emitted.

**Location in repo:** `config/ssdp_rules.json` (shipped with the project).  
Users may eventually override via a path in preferences; until then, editing the repo file
is the supported customization for developers and packagers.

## Relationship to code

- Loaded in `discovery/ssdp.py` (`_load_rules`).
- Applied during `_build_device_payload` **after** XML parsing:
  - **`name_rules`**: can override the displayed name when friendly/display names are ambiguous.
  - **`information_rules`**: builds the optional `information` string shown in details (concatenation of XML fields).
  - **`type_rules`**: overrides inferred `type` when header/XML text matches tokens.

Final category comes from the resolved `type` (see `DiscoveryManager._category_for_type`).

## Schema (conceptual)

### `name_rules`

| Field | Type | Meaning |
|-------|------|--------|
| `fallback_fields` | list of strings | Ordered list of `xml_fields` keys to use as name if earlier keys are empty. |
| `prefer_display_name_when_no_friendly_name` | bool | If `friendlyName` is empty but `displayName` is set, use `displayName`. |

### `information_rules`

| Field | Type | Meaning |
|-------|------|--------|
| `concat_fields` | list of strings | XML field names to join for the “Information” line (e.g. room + display). |
| `separator` | string | Placed between non-empty values. |

### `type_rules`

A list of objects, evaluated **in order**; the first match wins.

| Field | Type | Meaning |
|-------|------|--------|
| `contains_any` | list of strings | Substrings (lowercased) searched in a combined haystack: SSDP headers (`ST`, `NT`, `USN`, `SERVER`) and selected XML fields (`deviceType`, manufacturer, model, names, etc.). |
| `type` | string | Internal type id (e.g. `router`, `nas`, `smarttv`). Must be a type the app knows (see `DiscoveryManager._category_for_type` and UI type override menu). |

## How to extend

1. **Add a new type rule** for a vendor or device class: put more specific `contains_any`
   groups **before** generic ones so they take precedence.
2. **Use tokens** that actually appear in your network’s SSDP or XML (check logs or the
   SSDP details dialog).
3. **Keep types consistent** with `data/device_types.json` / icon mapping where applicable.
4. **Validate JSON** (trailing commas are invalid in JSON): use an editor or `python -m json.tool config/ssdp_rules.json`.

## What this file does *not* do

- It does not change UDP ports, multicast addresses, or M-SEARCH behavior.
- It does not replace SSDP → `Device` identity (that is `Device.key` and manager merge logic).
- It does not control offline timeouts (see `SSDP_INTEGRATION.md`).

## See also

- [`SSDP_INTEGRATION.md`](SSDP_INTEGRATION.md) — full SSDP pipeline in this app.  
- [`NetNeighbor_Brief_v1.4.md`](NetNeighbor_Brief_v1.4.md) — scope and “listen only” rule.
