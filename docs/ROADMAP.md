# Roadmap (development)

This file tracks **implementation** progress. Product vision and constraints live in
[`NetNeighbor_Brief_v1.4.md`](NetNeighbor_Brief_v1.4.md).

## Done (current baseline)

- GTK application shell, single-instance lock, locale activation socket (second launch raises existing window).
- **`DiscoveryManager`**: in-memory device cache, listener callbacks, SSDP merge rules, user overrides (type, monitored, last seen).
- **`Device` model**: stable SSDP identity (`UDN` / `USN` / MAC / endpoint fallback).
- **SSDP**: UDP multicast, M-SEARCH refresh, NOTIFY parsing, XML descriptor fetch, offline via NOTIFY byebye and TTL-based expiry (`2 × max-age` from `CACHE-CONTROL`, bounded), periodic refresh thread.
- **SSDP rules file** (`config/ssdp_rules.json`): name, information, and type heuristics without recompiling.
- **UI**: list and icon grid, category sidebar, details dialogs (SSDP / mDNS payload builders), browser open, user type override, icon source override, monitored devices, greyed offline when monitored, session notification history (in-memory) when desktop notifications are enabled.
- **Persistence** (per user): `~/.config/netneighbor/ui_prefs.json` (view, filters, overrides, monitored snapshots for restore on next launch — not a full device database file).
- **mDNS (zeroconf)**: real browser/listener, TXT decode, type/category heuristics, host-level service aggregation (single device can expose multiple services), and conditional presentation URL (only when `_http._tcp` is actually present).

## In progress / next

1. **Cross-protocol deduplication (UI)**  
   - `DeviceList` already bundles same `ip:port` for display; evaluate hostname / MAC for stronger merge when mDNS lands.

2. **Runtime mode decision (pre-packaging gate)**  
   - Decide the primary product form before packaging:
     - simple standalone app window
     - background service + optional UI
     - tray icon app
     - file manager integration entry point
     - other mode (`TBD`)
   - Define one primary mode for MVP and list optional modes as future variants.

3. **Packaging and release**  
   - AppImage / `.deb`, dependency checks, user-facing troubleshooting (see brief).

4. **Optional UX**  
   - Search / filter bar, copy IP, IPv6 (out of current MVP per brief but listed as future).

## How to use this file

- After a major feature lands, add a line under **Done** and adjust **In progress / next**.  
- Keep the brief in sync when scope or acceptance criteria change.
