# NetNeighbor — developer documentation

This folder contains technical documentation for contributors and maintainers. End-user
documentation remains in the repository root [`README.md`](../README.md).

## Documents

| Document | Purpose |
|----------|---------|
| [`NetNeighbor_Brief_v1.4.md`](NetNeighbor_Brief_v1.4.md) | Product scope, architecture overview, constraints, acceptance criteria (updated for current codebase). |
| [`ROADMAP.md`](ROADMAP.md) | Phases completed vs next, short-term priorities (especially mDNS). |
| [`SSDP_INTEGRATION.md`](SSDP_INTEGRATION.md) | SSDP protocol basics and how this app listens, parses, times out, and maps to `Device`. |
| [`MDNS_INTEGRATION.md`](MDNS_INTEGRATION.md) | mDNS browse/aggregation model, service mapping, URL rules, and lifecycle behavior. |
| [`SSDP_RULES_JSON.md`](SSDP_RULES_JSON.md) | `config/ssdp_rules.json`: purpose, schema, how to extend classification and naming. |
| [`UI_ARCHITECTURE.md`](UI_ARCHITECTURE.md) | GTK layer: main window, device list, threading, preferences, notifications history. |
| [`MAINTENANCE.md`](MAINTENANCE.md) | Ongoing care: logging, config paths, debugging discovery, suggested future docs. |
| [`CONTRIBUTING_ICONS.md`](CONTRIBUTING_ICONS.md) | Icon assets and `device_types.json` contributions (existing). |

## Quick orientation

- **Discovery**: `discovery/base.py` (contract), `discovery/manager.py` (cache + merges), `discovery/ssdp.py`, `discovery/mdns.py`.
- **UI**: `app.py` (single instance, activation), `ui/main_window.py`, `ui/device_list.py`, `ui/device_details.py`.
- **Config**: `~/.config/netneighbor/ui_prefs.json` (UI + overrides + monitored device snapshots for session-style restore).

If you add a new protocol, start from `BaseDiscovery` and register the provider in `DiscoveryManager`.
