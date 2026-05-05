# NetNeighbor — developer documentation

This folder contains technical documentation for contributors and maintainers. End-user
documentation remains in the repository root [`README.md`](../README.md) and
[`USER_DOCUMENTATION.md`](../USER_DOCUMENTATION.md).

## Documents

| Document | Purpose |
|----------|---------|
| [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md) | DiscoveryManager, override system, device commands model, protocol modules, data model, cross-protocol merge, config files, thread safety. |
| [`UI_ARCHITECTURE.md`](UI_ARCHITECTURE.md) | GTK layer: main window, device list, threading, preferences, notifications history. |
| [`SSDP.md`](SSDP.md) | SSDP protocol basics, M-SEARCH, XML parsing, TTL/offline handling, `ssdp_rules.json` schema. |
| [`MDNS.md`](MDNS.md) | mDNS browse/aggregation model, service mapping, URL rules, lifecycle, `mdns_rules.json` schema. |
| [`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md) | User overlays in `~/.config/netneighbor/` (rules + device types) plus notes on **`ui_prefs.json`** keys. |
| [`MAINTENANCE.md`](MAINTENANCE.md) | Ongoing care: logging, config paths, debugging discovery, release checklist. |
| [`PACKAGING.md`](PACKAGING.md) | Build and validate release artifacts (`.deb` + `tar.gz`) with helper scripts. |
| [`I18N.md`](I18N.md) | How to add/update translations (`.po`/`.mo`), merge catalogs, and test locales. |
| [`CONTRIBUTING_ICONS.md`](CONTRIBUTING_ICONS.md) | Icon assets and `device_types.json` contributions. |
| [`TODO.md`](TODO.md) | Known items and ideas for future work. |
| [`archive/`](archive/) | Archived documents (Brief v1.3, v1.4, ROADMAP, CHANGELOG). |

## Quick orientation

- **Discovery**: `discovery/base.py` (contract), `discovery/manager.py` (cache + merges + override system), `discovery/ssdp.py`, `discovery/mdns.py`, `discovery/wsd.py` (WS-Discovery; PyPI `WSDiscovery`), `discovery/netbios.py`.
- **UI**: `app.py` (single instance, activation), `ui/main_window.py`, `ui/device_list.py`, `ui/device_details.py`, `ui/tray_indicator.py` (panel icon), `utils/session_autostart.py` (XDG login entry).
- **Connect**: `utils/double_click_open.py` (target resolution), `utils/connect_launcher.py` (command templates + launch).
- **Config**:
  - `~/.config/netneighbor/ui_prefs.json` (UI state + user choices/rules/overrides including `device_commands`)
  - `~/.config/netneighbor/discovery.json` (per-protocol `mdns`/`ssdp` blocks with `enabled`/`rules`, `merge.protocol_order`, `merge.information_precedence`, plus `startup_refresh_seconds`; see [`MAINTENANCE.md`](MAINTENANCE.md))
  - `~/.cache/netneighbor/discovery-cache.json` (volatile discovery cache: `last_seen`, monitored snapshots, SSDP XML/profile cache)
  - `config/default_commands.json` (built-in per-scheme command templates; loaded by `utils/connect_launcher.py`)

If you add a new protocol, start from `BaseDiscovery` and register the provider in `DiscoveryManager`.
