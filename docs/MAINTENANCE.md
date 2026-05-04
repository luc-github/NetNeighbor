# Maintenance and follow-up

This complements [`README.md`](../README.md) and the [developer index](README.md) for anyone
keeping the project healthy between releases.

## Configuration paths

| Path | Content |
|------|---------|
| `~/.config/netneighbor/ui_prefs.json` | UI state + per-device overrides + rules (not a full device DB). Tray-related booleans include **`close_to_tray`** (default true), **`start_minimized_to_tray`**, **`start_at_login`** (writes XDG autostart when enabled). |
| `~/.config/autostart/io.esp3d.netneighbor.desktop` | Present when **Start NetNeighbor when logging in** is checked in View → Preferences; standard XDG autostart entry. **`Exec=`** appends **`--start-minimized-to-tray`** (tray-only login start) while the **menu** `.desktop` from packaging stays without that flag. Implemented in `utils/session_autostart.py`. |
| `~/.cache/netneighbor/discovery-cache.json` | Volatile discovery cache (`last_seen_overrides`, monitored snapshots metadata, SSDP XML/profile cache). Safe to delete; app rebuilds it. |
| `~/.config/netneighbor/logging.json` | Per-area log levels (`default`, `app`, `ssdp`, `mdns`). Created with defaults on first run if missing. |
| `~/.cache/netneighbor/netneighbor.log` | Log file when file logging is enabled (see `app.py`). |
| `~/.cache/netneighbor/remote_icons/` | On-disk cache for **device-provided** icons fetched from SSDP/mDNS URLs (normalized). Primary file per URL: **`{SHA256(url)}.payload`** raw HTTP body (decoded with GdkPixbuf on load — avoids flaky `savev`). The digest uses a **canonical URL** after rewriting typical LAN hosts (`*.local`, `*.lan`, single-label names) to the **device IP** (stable fetch + hash), then normalizing (lowercased scheme/host, trailing FQDN dot stripped, **default HTTP/HTTPS ports omitted**). Disk lookup also tries **legacy explicit `:80` / `:443`** variants so filenames from older canonicalization rules still resolve. A second lookup tries the **canonical raw URL** (pre-IP rewrite) for older payloads. **HTTPS** to `*.local` / `*.lan` or **RFC1918** hosts uses a relaxed TLS verify context (typical self-signed printer certs). Legacy **`.png`** files from older releases are still read if present. Clearing the folder forces a fresh download; if icons look wrong after a firmware change, clear **`remote_icon_index.json`** (see next row) as well. |
| `~/.cache/netneighbor/remote_icon_index.json` | **Host → icon cache index** (version 2 JSON): per **IPv4/IPv6 key** stores **`canonical_url`**, **`payload_sha256`** (same stem as **`remote_icons/{sha256}.payload`**), and **`updated_at`**. Lets the UI load the correct cached icon **on cold start** without waiting for every mDNS TXT variant. Delete this file to drop remembered host→URL mappings (payload files remain until removed manually). |
| `~/.config/netneighbor/discovery.json` | Discovery toggles + startup refresh schedule. Top-level **`mdns`** / **`ssdp`** with **`enabled`**, **`rules`**, optional **`query`**: SSDP **`interval_seconds`** (periodic M-SEARCH cadence, default 60), **`mx_seconds`** (M-SEARCH **MX** max wait, clamped **5–6**, default 5), **`descriptor_http_min_interval_seconds`** (minimum gap between HTTP GETs for descriptor URLs whose **host is the same IP** (or same hostname if not numeric); avoids duplicate fetches when anticipatory XML and SSDP LOCATION arrive close together; **0** disables; default **5**, max **120**); mDNS **`enumeration_timeout_seconds`** (DNS-SD type scan, **2–3** s, default 2.5), **`enumeration_interval_seconds`** (repeat scan, default 240), **`service_info_timeout_ms`** (`get_service_info`, **2000–3000** ms, default 2500). **`merge.protocol_order`**: ordered protocol **`source`** ids (default **`ssdp`**, **`mdns`**) — earlier = stronger for live-row tie-breaks. **`merge.information_precedence`**: ordered roles (**`user_override`**, **`ssdp_live`**, **`ssdp_profile_cache`**, **`mdns`**) — must list all four exactly once to customize; defaults favour user prefs, then live SSDP, then disk-cache hints on mDNS, then raw mDNS (see `utils/discovery_config.py`). **`startup_refresh_seconds`** at root (comma-separated string or JSON array). |

## Logging

- Root logging is configured in `app.py` (console + optional rotating file under cache dir).
- **`logging.json`**: keys `ssdp` and `mdns` apply both to the protocol module (`discovery.ssdp`, `discovery.mdns`) and to manager-side lines for that source (`discovery.manager.ssdp`, `discovery.manager.mdns`), including “Device added/updated”, **location** resolution (`Location: …`), and **device appearance** (type, bundled icon filename, `user_location`). Set **`app`** to **DEBUG** for **`ui.device_list`** messages (remote vs bundled icon path, GTK theme fallback).
- Special levels **`NONE`**, **`OFF`**, **`DISABLED`**, **`SILENT`** turn off that area (no INFO/DEBUG/WARNING from those loggers).
- **`NETNEIGHBOR_LOG_LEVEL`** still overrides the root/default level when set.
- Other useful loggers: `discovery.manager` (start/stop, publishing at DEBUG), `ui`.

## Systray, window chrome, fullscreen

- **Tray**: `ui/tray_indicator.py` — prefers Ayatana **AppIndicator** (`gir1.2-ayatanaappindicator3-0.1`) or legacy **AppIndicator3** (`gir1.2-appindicator3-0.1`), else **Gtk.StatusIcon**. Tray menu: **Open** / **Minimize to tray** (sensitive while the window is visible) / **Quit**. Without any of these backends, **close-to-tray** cannot hide to the panel; the window still closes normally.
- **Icons**: canonical vector logo — **`assets/svg/netneighbor.svg`**. Theme resolution uses **`assets/icons/`** as an extra icon search path; **`hicolor/scalable/apps/`** contains symlinks **`io.esp3d.netneighbor.svg`** and **`io.esp3d.netneighbor-tray.svg`** pointing at that file (so GTK finds app + tray names). Installed `.deb` copies the SVG into `/usr/share/icons/hicolor/scalable/apps/` under both names.
- **Close-to-tray + tray available**: main window uses a **Gtk.HeaderBar** with **Maximize** and **Close** only (explicit minimize lives on the tray menu). **CLI** **`--start-minimized-to-tray`** and first-run flow can skip stealing focus until the user opens from the tray (`app.py` → `MainWindow`). **F11** toggles fullscreen (useful for the icon grid); implemented via a window **Gtk.AccelGroup** in `ui/main_window.py`.
- **`utils/gtk_dialog.py`**: **`prepare_gtk_dialog`** disables CSD/header-bar dialogs that lose WM title bars on some compositors/window managers (used by main window, details, list, and related dialogs).
- **Open on PCs**: context-menu / double-click **Open** is suppressed for merged rows whose primary **type** is **`computer`** (WSD metadata URL on port 5357 is not a useful browser target yet); double-click opens **Details** instead (`ui/device_list.py`).

## Common tasks

- **Rules tuning**: shipped `config/ssdp_rules.json` / `config/mdns_rules.json`; user overlays in **`~/.config/netneighbor/`** — [`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md).
- **Icons / types**: `data/device_types.json` and [`CONTRIBUTING_ICONS.md`](CONTRIBUTING_ICONS.md); optional user merge from `~/.config/netneighbor/device_types.json` ([`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md)). Icons loaded from SSDP/mDNS URLs are persisted under **`~/.cache/netneighbor/remote_icons/`** with a **`remote_icon_index.json`** host map (see table above).
- **Translations**: `locale/*/LC_MESSAGES/netneighbor.po`, compile with `msgfmt`; see [`I18N.md`](I18N.md) for full workflow.

## Discovery extension hooks

- **`DiscoveryManager.register_presence_transition_hook` / `unregister_presence_transition_hook`** (`discovery/manager.py`): optional observers for **`"online"`** / **`"offline"`** transitions per device identity row (not wired in core UI). Callbacks run on the same thread as the discovery update — use `GLib.idle_add` before touching GTK. Failures are logged and do not stop discovery. Planned use: future plugins / alerting (see [`ROADMAP.md`](ROADMAP.md) « Future — plugins »). Extension code may import `DiscoveryManager`, `PresenceTransitionHook`, and `PresenceTransitionKind` from the **`discovery`** package (`from discovery import …`).

## Debugging discovery issues

1. Confirm only **one** app instance (SSDP bind + multicast).
2. Check firewall allows UDP 1900 inbound / multicast.
3. Inspect SSDP details in the UI for `LOCATION`, `USN`, XML presence.
4. Compare logs for “XML fetched” vs failures (timeouts, parse errors).
5. **Location looks like a URL / `description.xml`**: should no longer happen; if prefs were polluted earlier, open **Location presets** or edit **`ui_prefs.json`** — invalid URL-like presets are stripped on load. **`utils/location_label.py`** documents the rejection rules.
6. **Wrong or delayed device icon**: confirm **`remote_icons/*.payload`** and **`remote_icon_index.json`** together; bump **`mdns`** / **`app`** to **DEBUG** to trace aggregate + icon load path.
7. **Device details (first tab)**: duplicate **Last seen** rows are collapsed to the latest timestamp; bundled WSD/wsdd/NMB-only rows omit legacy **Discovery** / **WSD XAddrs** / **NetBIOS registration** lines (overview + protocol fields stay minimal). **IPv6 (link-local)** in the overview only appears when a `fe80::` address is inferred from device IP or WSD/wsdd payloads — many Windows PCs only advertise IPv4 in WSD URLs, so an empty IPv6 line there is expected.

## Changelog / release notes

- [`CHANGELOG.md`](CHANGELOG.md) tracks versioned highlights (since **0.8.0**).

## Suggested additional docs (optional)

| Document | When to add |
|----------|-------------|
| `TESTING.md` | Once you have automated tests or a repeatable manual checklist per release. |
| `PACKAGING.md` | When AppImage / `.deb` pipeline is scripted (commands, deps, smoke test). |
| `TROUBLESHOOTING.md` | User-facing FAQ distilled from GitHub issues (short). |

Architecture Decision Records (ADR): optional short files under `docs/adr/` for major choices
(why GTK3, why in-memory store first, etc.) if the team grows.

## Release checklist (minimal)

- [ ] `README.md` and brief aligned with behavior.
- [ ] **`VERSION`** bumped; **`docs/CHANGELOG.md`** entry for the release.
- [ ] `docs/MAINTENANCE.md`, `docs/PACKAGING.md`, and `docs/COMMUNITY_OVERRIDES.md` aligned if behavior or deps changed.
- [ ] `docs/ROADMAP.md` updated.
- [ ] Smoke run: start app, discovery, details, tray hide/show, **CLI** `--start-minimized-to-tray`, optional autostart toggle, monitored device restart.
- [ ] Packaging smoke: `./packaging/build_deb.sh`, install in VM, verify icons + `Recommends` (tray + `nmblookup`).
