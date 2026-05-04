# User overlays (`~/.config/netneighbor/*.json`)

NetNeighbor ships defaults under `data/` and `config/`. You can **extend or override** the same
schemas from your home directory without editing the install; app updates keep your files.

**Directory:** `~/.config/netneighbor/` (created automatically when you save other prefs; JSON
files here are optional).

Even when NetNeighbor is installed in a **read-only** location (flatpak `/app`, distro package under
`/usr`, etc.), you can still add or edit overlays: only your config directory needs to be writable, not the install prefix.

## Application preferences (`ui_prefs.json`)

This file is **not** part of the overlay merge system (`data/` / `config/`). NetNeighbor reads and
writes it automatically. You may edit it manually **while the app is closed** if you need to
bulk-reset or script defaults; invalid or missing keys fall back to built-in defaults on next start.

| Key (examples) | Meaning |
|----------------|---------|
| `close_to_tray` | If true and a tray icon exists, closing the window hides the app instead of exiting (default true). |
| `start_minimized_to_tray` | If true and tray works, start hidden to the panel after launch. |
| `start_at_login` | If true, ensures `~/.config/autostart/io.esp3d.netneighbor.desktop` exists (see [`MAINTENANCE.md`](MAINTENANCE.md)). |
| `autostart_onboarding_done` | Set after the first-run **session startup** dialog; prevents showing that prompt again. |

Other keys store view mode, icon sort, overrides, sidebar width, notification mode, etc.; treat
unknown keys as opaque unless you grep the codebase for `save_ui_preferences` / `ui_prefs`.

## Rule and type overlay files

| File | Merges with | Behaviour |
|------|-------------|-----------|
| `device_types.json` | `data/device_types.json` | Deep-merge per `mdns` / `ssdp` service key; shallow merge `fallback`. |
| `ssdp_rules.json` | `config/ssdp_rules.json` | Shallow merge `name_rules` and `information_rules` (user keys win). **`type_rules`:** user list is **prepended** (first match wins over shipped rules). |
| `mdns_rules.json` | `config/mdns_rules.json` | **`summary_from_txt`:** same label merges `keys` lists (user aliases first, then bundled). **`type_rules`:** user list **prepended**. |

## Partial files

Overlay JSON may omit entire sections — only supplied keys/objects participate in the merge.

## Validation

Validate before saving:

```bash
python -m json.tool ~/.config/netneighbor/mdns_rules.json
```

Broken JSON logs a warning and is ignored.

## Complete examples (optional overlays)

These files are **fully valid** on their own under `~/.config/netneighbor/`. In practice you
often ship a **minimal** overlay (only the keys you need); the merge fills the rest from the
bundled app versions.

### `~/.config/netneighbor/mdns_rules.json`

User rules are **merged** with [`config/mdns_rules.json`](../config/mdns_rules.json): your
`summary_from_txt` rows add or extend labels (same `label` string merges `keys`); your
`type_rules` are evaluated **before** those from the app.

```json
{
  "summary_from_txt": [
    {
      "label": "Manufacturer",
      "keys": ["oem", "brand", "mfg", "manufacturer", "make"]
    },
    {
      "label": "Model",
      "keys": ["product", "mdl", "model", "ty"]
    },
    {
      "label": "Serial number",
      "keys": ["serial", "sn", "serialnumber"]
    }
  ],
  "type_rules": [
    {
      "contains_any": ["myvendor-nas", "storageserver"],
      "type": "nas"
    },
    {
      "contains_any": ["custom-gw", "gw-acme"],
      "type": "router"
    }
  ]
}
```

### `~/.config/netneighbor/ssdp_rules.json`

Merged with [`config/ssdp_rules.json`](../config/ssdp_rules.json). `name_rules` and
`information_rules` behave like shallow dict overlays (your keys replace the bundled value for the
same key). `type_rules`: your entries are **prepended**, so they win over shipped rules when the
haystack matches.

```json
{
  "name_rules": {
    "fallback_fields": [
      "friendlyName",
      "displayName",
      "modelName"
    ],
    "prefer_display_name_when_no_friendly_name": true
  },
  "information_rules": {
    "concat_fields": [
      "roomName",
      "displayName",
      "modelDescription"
    ],
    "separator": " — "
  },
  "type_rules": [
    {
      "contains_any": [
        "my-upnp-gateway",
        "acme-router-firmware"
      ],
      "type": "router"
    },
    {
      "contains_any": [
        "custom-media-renderer",
        "vendor-dlna-box"
      ],
      "type": "mediaserver"
    }
  ]
}
```

Use **real tokens** from your LAN (inspect device details → raw XML / TXT); replace the
placeholder substrings (`myvendor-nas`, `my-upnp-gateway`, …) with strings that appear in the
combined haystack (see [`SSDP_RULES_JSON.md`](SSDP_RULES_JSON.md) and [`MDNS_RULES_JSON.md`](MDNS_RULES_JSON.md)).

## Contributing upstream

Patterns that help others belong in merge requests updating the bundled `config/` or `data/`
files; see [`CONTRIBUTING_ICONS.md`](CONTRIBUTING_ICONS.md), [`SSDP_RULES_JSON.md`](SSDP_RULES_JSON.md),
and [`MDNS_RULES_JSON.md`](MDNS_RULES_JSON.md).
