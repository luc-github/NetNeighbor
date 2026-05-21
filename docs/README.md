# NetNeighbor — developer documentation

This folder contains technical documentation for contributors and maintainers. End-user
documentation is in [`USER_DOCUMENTATION.md`](USER_DOCUMENTATION.md).

## Structure

```
docs/
  reference/      Protocol and architecture deep-dives
  contributing/   How to contribute icons, translations, and rule overlays
  operations/     Build, packaging, and maintenance guides
  archive/        Historical documents (GTK 1.x era)
  images/         Screenshots and logos
  esp3d-overview/ ESP3D project overview pages
```

## User and project

| Document | Purpose |
|----------|---------|
| [`USER_DOCUMENTATION.md`](USER_DOCUMENTATION.md) | End-user guide: UI, menus, preferences, tray. |
| [`ROADMAP.md`](ROADMAP.md) | Planned and completed work, backlog, ideas. |

## Reference

| Document | Purpose |
|----------|---------|
| [`reference/BACKEND_ARCHITECTURE.md`](reference/BACKEND_ARCHITECTURE.md) | DiscoveryManager, override system, device commands model, protocol modules, data model, cross-protocol merge, config files, thread safety. |
| [`reference/SSDP.md`](reference/SSDP.md) | SSDP protocol basics, M-SEARCH, XML parsing, TTL/offline handling, `ssdp_rules.json` schema. |
| [`reference/MDNS.md`](reference/MDNS.md) | mDNS browse/aggregation model, service mapping, URL rules, lifecycle, `mdns_rules.json` schema. |
| [`reference/WSD.md`](reference/WSD.md) | WS-Discovery (Windows PCs, printers), EPR/scope parsing, type classification, offline detection. |
| [`reference/NETBIOS.md`](reference/NETBIOS.md) | NetBIOS name browse via `nmblookup`, suffix table, integration as supplementary source. |

## Contributing

| Document | Purpose |
|----------|---------|
| [`contributing/COMMUNITY_OVERRIDES.md`](contributing/COMMUNITY_OVERRIDES.md) | User overlays in `~/.config/netneighbor/` (rules + device types) plus notes on **`ui_prefs.json`** keys. |
| [`contributing/CONTRIBUTING_ICONS.md`](contributing/CONTRIBUTING_ICONS.md) | Icon assets and `device_types.json` contributions. |
| [`contributing/I18N.md`](contributing/I18N.md) | How to add/update translations (`.po`/`.mo`), merge catalogs, and test locales. |

## Operations

| Document | Purpose |
|----------|---------|
| [`operations/MAINTENANCE.md`](operations/MAINTENANCE.md) | Ongoing care: logging, config paths, debugging discovery, release checklist. |
| [`operations/PACKAGING.md`](operations/PACKAGING.md) | Build and validate release artifacts (`.deb` / `tar.gz` / AppImage / PyInstaller / Inno Setup). |

## Archive

| Document | Purpose |
|----------|---------|
| [`archive/ROADMAP_QT_2_0.md`](archive/ROADMAP_QT_2_0.md) | Completed Qt 2.0 migration checklist (GTK → PySide6). |
| [`archive/UI_ARCHITECTURE.md`](archive/UI_ARCHITECTURE.md) | GTK 3 UI layer — archived, no longer current. |
| [`archive/CHANGELOG.md`](archive/CHANGELOG.md) | Release notes up to 0.8.0. |
| [`archive/`](archive/) | Brief v1.3, v1.4, GTK roadmap. |

## Quick orientation

- **Discovery**: `app/discovery/base.py` (contract), `app/discovery/manager.py` (cache + merges + override system), `app/discovery/ssdp.py`, `app/discovery/mdns.py`, `app/discovery/wsd.py` (WS-Discovery), `app/discovery/netbios.py`.
- **UI**: `app/app_qt.py` (single instance, activation), `app/ui/` (`NetNeighborMainWindow`, `MainThreadScheduler`, `PreferencesDialog`, `NetNeighborTray`, …), `app/utils/session_autostart.py` (XDG/registry login entry).
- **Connect**: `app/utils/double_click_open.py` (target resolution), `app/utils/connect_launcher.py` (command templates + launch).
- **Config**:
  - `~/.config/netneighbor/ui_prefs.json` (UI state + user choices/rules/overrides including `device_commands`)
  - `~/.config/netneighbor/discovery.json` (per-protocol `mdns`/`ssdp` blocks with `enabled`/`rules`, `merge.protocol_order`, `merge.information_precedence`, plus `startup_refresh_seconds`; see [`operations/MAINTENANCE.md`](operations/MAINTENANCE.md))
  - `~/.cache/netneighbor/discovery-cache.json` (volatile discovery cache: `last_seen`, monitored snapshots, SSDP XML/profile cache)
  - `app/config/default_commands.json` (per-OS built-in scheme templates; loaded by `app/utils/connect_launcher.py`)

If you add a new protocol, start from `BaseDiscovery` and register the provider in `DiscoveryManager`.
