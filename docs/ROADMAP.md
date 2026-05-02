# Roadmap (development)

This file tracks **implementation** progress. Product vision and constraints live in
[`NetNeighbor_Brief_v1.4.md`](NetNeighbor_Brief_v1.4.md).

## Done (current baseline)

- GTK application shell, single-instance lock, locale activation socket (second launch raises existing window).
- **`DiscoveryManager`**: in-memory device cache, listener callbacks, SSDP merge rules, user overrides (type/name/location/monitored/last seen), cross-protocol host identity keys (`UID`/`MAC`/IP fallback) for overrides.
- **`Device` model**: stable SSDP identity (`UDN` / `USN` / MAC / endpoint fallback).
- **SSDP**: UDP multicast, M-SEARCH refresh, NOTIFY parsing, XML descriptor fetch, offline via NOTIFY byebye and TTL-based expiry (`2 × max-age` from `CACHE-CONTROL`, bounded), periodic refresh thread.
- **SSDP rules file** (`config/ssdp_rules.json`): name, information, and type heuristics without recompiling.
- **UI**: list and icon grid, sidebar grouped by type or location (matching arrange mode), details dialogs (SSDP / mDNS payload builders), browser open, user type/name/location overrides, icon appearance management (system/provided/custom + picker), monitored devices, greyed offline when monitored, session notification history (in-memory) available from `Tools`.
- **Persistence** (per user): `~/.config/netneighbor/ui_prefs.json` (view, filters, overrides, monitored snapshots for restore on next launch — not a full device database file).
- **mDNS (zeroconf)**: real browser/listener, TXT decode, type/category heuristics, host-level service aggregation (single device can expose multiple services), and conditional presentation URL (only when `_http._tcp` is actually present). Aggregation scoring prefers **playback / printer** services (e.g. `_airplay._tcp`, `_raop._tcp`, Sonos/Cast/Spotify Connect, `_ipp._tcp`, `_printer._tcp`) over bare **`_http._tcp`** so representative TXT, type, and icon metadata stabilize sooner on multi-service hosts.
- **`data/device_types.json`**: explicit mDNS keys for common smart-speaker / casting services map to **Smart Speakers** and bundled **`smartspeaker.png`** (user overlay still merges from `~/.config/netneighbor/device_types.json`).
- **Auto location (metadata)**: room-like labels are inferred from SSDP XML / mDNS TXT (including per-service TXT under `metadata["services"]`). Values that look like **SSDP LOCATION / descriptor URLs** (`http…`, `description.xml`, etc.) are rejected so they never populate **location** UI or presets (`utils/location_label.py`).
- **Remote icon persistence**: SSDP/mDNS device icon URLs normalized and cached under `~/.cache/netneighbor/remote_icons/` (raw **`.payload`** per SHA-256 of a **canonical URL**). **Canonicalization** omits default HTTP/HTTPS ports; lookup also tries explicit **`:80` / `:443`** forms when the same logical URL appears with or without a default port. A **host-level RAM cache** and **`~/.cache/netneighbor/remote_icon_index.json`** map **LAN IP → `{canonical_url, payload_sha256, updated_at}`** so the correct **`.payload`** loads immediately at startup even when the representative mDNS port or advertised URL string drifts. UI tries **all** icon URLs from merged service TXT before falling back to bundled / GTK icons.
- **Discovery / UI debugging (optional)**: `logging.json` keys **`mdns`** / **`ssdp`** at **DEBUG** surface manager lines for **location** resolution and **device appearance** (type, icon, `user_location`); **`app`** at **DEBUG** includes **`ui.device_list`** lines for bundled vs remote icon paths and GTK fallbacks.
- **`DiscoveryManager.register_presence_transition_hook`** (internal extension point): optional callbacks when a cached device transitions **online** or **offline** (no hooks registered by core UI yet). Intended for future plugins / scripting; callers may run off the GTK thread.
- **Location stability hardening**: location cache is now preferred and persisted automatically; discovery updates can promote a new plausible room value and trigger cache reapply/persist, reducing `No location` flicker during metadata races.
- **Identity gating (early discovery)**: first-seen SSDP/mDNS rows without stable identity (`UID`/MAC) are briefly held (~3s) before UI publication; immediate release occurs once identity resolves.
- **Release/version unification**: single root `VERSION` file is now consumed by About dialog, build scripts, and desktop entry templating; release artifacts embed `VERSION` explicitly.
- **Packaging quality updates**: `.deb` now sets `Installed-Size`; launcher icon install matches hicolor size buckets (64 + 256); manual user-data cleanup helper script added for optional full local wipe.
- **Discovery cache split**: volatile data moved to `~/.cache/netneighbor/discovery-cache.json` (`last_seen`, monitored snapshots metadata, SSDP XML/profile cache), while `ui_prefs.json` keeps UI/user choices.
- **SSDP startup enrichment**: persistent SSDP XML/profile cache reused at startup to improve immediate name/type/details rendering before fresh XML fetches complete.
- **Startup recovery refreshes**: configurable one-shot refreshes after discovery startup via root **`startup_refresh_seconds`** in `discovery.json` (string or array; default `[20, 45, 90]`).

## In progress / next

1. **Cross-protocol deduplication hardening**  
   - Host identity-based override keys are in place (`UID`/`MAC`/IP fallback).  
   - Recent work improved **same-host mDNS** stability (aggregate representative scoring, host icon index); continue validation on real networks for edge cases (sleep/wake, endpoint shifts, mixed protocol timing, SSDP+mDNS bundle races).

2. **Runtime mode (baseline chosen)**  
   - **Primary mode:** standalone GTK window (single instance); matches current implementation and MVP packaging path.  
   - **Deferred variants:** optional tray/minimize-to-background later; heavier split (system service + UI) or file-manager integration only if needs clearly justify the maintenance cost.

3. **Packaging and release**  
   - AppImage track + optional lint/signing/checksum automation around current `.deb`/`tar.gz` flow (see brief).

4. **Optional UX**  
   - Search / filter bar, copy IP, IPv6 (out of current MVP per brief but listed as future).

## Future — plugins / automation / monitoring (planned, after current next items)

Goals (examples gathered from design discussion):

- **Presence monitoring:** react to connects/disconnects (e.g. notify by email when a monitored server drops).
- **Port-triggered actions:** when a known service/port appears on a host, run a **user-configured** shell command with `IP` / `PORT` (e.g. open FTP client, ssh, Telnet UI).
- Prefer a phased approach: stabilize **presence hooks**, then optionally **shell/exec hooks with env vars**, before a fuller dynamic plugin loader if needed.

Architecture / safety reminders (carry into implementation specs):

| Topic | Guidance |
|------|----------|
| **Safety** | Never execute payloads from the network. Only configs the user opted into; whitelist identities (IP / UID) where possible. |
| **Stability** | Isolate hook failures (`try`/`except` per hook); timeouts so slow plugins cannot block discovery. Presence hooks today already swallow exceptions and log (`discovery.manager`). |
| **Threading** | Protocol paths can call hooks off the GTK main thread — plugins must marshal UI work via `GLib.idle_add` (see `DiscoveryManager.register_presence_transition_hook` docstring). |
| **Phasing** | (1) stable event vocabulary from core; (2) optional external scripts triggered with fixed env/schema; (3) richer plugin API only if demand is clear. |

## How to use this file

- After a major feature lands, add a line under **Done** and adjust **In progress / next**.  
- Keep the brief in sync when scope or acceptance criteria change.
