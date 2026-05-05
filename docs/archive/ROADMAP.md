# Roadmap (development)

This file tracks **implementation** progress. Product vision and constraints live in
[`NetNeighbor_Brief_v1.4.md`](NetNeighbor_Brief_v1.4.md).

## Done (current baseline)

- **0.8.0 (2026-05)** — **[`CHANGELOG`](CHANGELOG.md)**: kernel **neighbor MAC** assist for merge + **IPv4** when discovery showed **IPv6** only; **IPv6 synthetic WSD/wsdd** **`nmblookup -A`** queue; provisional row **pulse** + icon-load affordance; **`prepare_gtk_dialog`**; **first-run hide to tray**; **`--start-minimized-to-tray`** + autostart **`Exec`** vs menu launcher; tray **Minimize to tray**; **`app.py`** known-args parsing + forwarding remainder to **`Gtk.Application.run`**.
- GTK application shell, single-instance lock, locale activation socket (second launch raises existing window).
- **`DiscoveryManager`**: in-memory device cache, listener callbacks, SSDP merge rules, user overrides (type/name/location/monitored/last seen), cross-protocol host identity keys (`UID`/`MAC`/IP fallback) for overrides.
- **`Device` model**: stable SSDP identity (`UDN` / `USN` / MAC / endpoint fallback).
- **SSDP**: UDP multicast, M-SEARCH refresh, NOTIFY parsing, XML descriptor fetch, offline via NOTIFY byebye and TTL-based expiry (`2 × max-age` from `CACHE-CONTROL`, bounded), periodic refresh thread.
- **SSDP rules file** (`config/ssdp_rules.json`): name, information, and type heuristics without recompiling.
- **Additional discovery stacks**: **WSD** (`discovery/wsd.py`, PyPI `WSDiscovery`), **`wsdd`** local daemon cache (`discovery/wsdd_client.py`), **NetBIOS name browse** via **`nmblookup`** (`discovery/netbios.py`); enabled/query knobs under **`discovery.json`** (see [`MAINTENANCE.md`](MAINTENANCE.md)).
- **UI**: list and icon grid, sidebar grouped by type or location (matching arrange mode), details dialogs (**Overview** + protocol fields / raw XML / mDNS **Services** / icon tab), **`utils/details_payload`**: merged **Last seen** deduped; WSD/wsdd/NMB-only detail rows minimized (no noisy Discovery/XAddrs/NetBIOS registration lines).
- **Browser / file-manager open**: **Open** / double-click resolves via **`utils/double_click_open`** (HTTP(S) → SMB → FTP → SSH → Telnet; **`computer`** → **`smb://`** when nothing else matches). No fallback to **Details** on double-click.
- **Tray & session GTK**: **`ui/tray_indicator.py`** (AppIndicator / StatusIcon fallback; tray **Minimize to tray**), **`utils/session_autostart.py`** (XDG autostart `.desktop` with **`--start-minimized-to-tray`**). **View → Preferences**: close-to-tray (default), start minimized to tray, start at login.
- **Window chrome**: with tray active, **`Gtk.HeaderBar`** **Maximize + Close** (minimize via tray menu); **F11** fullscreen for icon-heavy use.
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
- **Packaging quality updates**: `.deb` sets `Installed-Size`; launcher + tray icons ship as **`hicolor/scalable/apps`** **SVG** (see `assets/svg/netneighbor.svg`); **`control`** declares **`Recommends`**: `samba-common-bin` (**`nmblookup`**), **`gir1.2-ayatanaappindicator3-0.1` | `gir1.2-appindicator3-0.1`** (tray GObject bindings — not Samba servers). Manual user-data cleanup helper script retained for optional full local wipe (see [`PACKAGING.md`](PACKAGING.md)).
- **Discovery cache split**: volatile data moved to `~/.cache/netneighbor/discovery-cache.json` (`last_seen`, monitored snapshots metadata, SSDP XML/profile cache), while `ui_prefs.json` keeps UI/user choices.
- **SSDP startup enrichment**: persistent SSDP XML/profile cache reused at startup to improve immediate name/type/details rendering before fresh XML fetches complete.
- **Startup recovery refreshes**: configurable one-shot refreshes after discovery startup via root **`startup_refresh_seconds`** in `discovery.json` (string or array; default `[20, 45, 90]`).

## In progress / next

1. **Cross-protocol deduplication hardening**  
   - Host identity-based override keys are in place (`UID`/`MAC`/IP fallback).  
   - Recent work improved **same-host mDNS** stability (aggregate representative scoring, host icon index); continue validation on real networks for edge cases (sleep/wake, endpoint shifts, mixed protocol timing, SSDP+mDNS bundle races).

2. **Runtime mode extensions**  
   - **Delivered:** single-instance GTK app + optional **systray** + **session autostart** (no standalone systemd daemon).  
   - **Still deferred:** headless discovery **service** with separate UI attachment, deeper **file-manager** integration — only if cost/benefit is clear.

3. **Packaging and release**  
   - **Done for Debian family:** scripted `.deb` + `tar.gz` + `release.sh`, SVG icons, **`Recommends`** for tray + NetBIOS client.  
   - **Still open:** AppImage (or Flatpak), optional lint/signing/checksum hardening beyond current `SHA256SUMS`.

4. **Optional UX / data**  
   - Search / filter bar, quick **copy IP** action, richer **IPv6** surfaces when protocols expose more than link-local inference (today: overview **IPv6 (link-local)** when `fe80::` appears in payloads or device IP).

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
