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
- **mDNS (zeroconf)**: real browser/listener, TXT decode, type/category heuristics, host-level service aggregation (single device can expose multiple services), and conditional presentation URL (only when `_http._tcp` is actually present).
- **Remote icon persistence**: SSDP/mDNS device icon URLs normalized and cached under `~/.cache/netneighbor/remote_icons/` (raw `.payload` per SHA-256 of the URL; optional read of legacy `.png`).
- **`DiscoveryManager.register_presence_transition_hook`** (internal extension point): optional callbacks when a cached device transitions **online** or **offline** (no hooks registered by core UI yet). Intended for future plugins / scripting; callers may run off the GTK thread.

## In progress / next

1. **Cross-protocol deduplication hardening**  
   - Host identity-based override keys are in place (`UID`/`MAC`/IP fallback).  
   - Continue validation on real networks for edge cases (sleep/wake, endpoint shifts, mixed protocol timing).

2. **Runtime mode (baseline chosen)**  
   - **Primary mode:** standalone GTK window (single instance); matches current implementation and MVP packaging path.  
   - **Deferred variants:** optional tray/minimize-to-background later; heavier split (system service + UI) or file-manager integration only if needs clearly justify the maintenance cost.

3. **Packaging and release**  
   - AppImage / `.deb`, dependency checks, user-facing troubleshooting (see brief).

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
