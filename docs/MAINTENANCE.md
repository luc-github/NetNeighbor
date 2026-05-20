# Maintenance and follow-up

This complements [`README.md`](../README.md) and the [developer index](README.md) for anyone
keeping the project healthy between releases.

## Configuration paths

> **All platforms** (Windows, Linux, macOS) use `~` = `Path.home()` as the base, so paths are
> identical relative to the home directory. On Windows `~` resolves to `C:\Users\<name>`.
>
> **Windows path consistency (fixed in 2.0)** — an earlier version of `utils/device_remote_icon.py`
> computed the icon cache base as `%LOCALAPPDATA%\netneighbor\cache\` on Windows, while
> `utils/app_logging.py` and `utils/discovery_cache.py` both used `Path.home() / ".cache" /
> "netneighbor"`. This caused icon payload files and the index to land in
> `C:\Users\<name>\AppData\Local\netneighbor\cache\remote_icons\` instead of
> `C:\Users\<name>\.cache\netneighbor\remote_icons\` like everything else.
> The fix was to remove the `sys.platform == "win32"` branch from `_netneighbor_cache_dir()` in
> `device_remote_icon.py` so all three modules resolve to the same `~/.cache/netneighbor/` base.
> Users who ran the old version on Windows may have orphaned files in
> `%LOCALAPPDATA%\netneighbor\cache\` — those can be deleted; the app will re-fetch icons into the
> correct location automatically.

| Path | Content |
|------|---------|
| `~/.config/netneighbor/ui_prefs.json` | UI state + per-device overrides + rules (not a full device DB). Tray-related booleans include **`close_to_tray`** (default true), **`start_minimized_to_tray`**, **`start_at_login`** (writes XDG autostart when enabled). **`custom_command_template`** (**Tools → External applications**) for **Run custom command**; **`connect_command_templates`** overrides **Open** per URL scheme; **`device_commands`** stores per-device connection command lists from the **Options** tab (see [`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md)). |
| `~/.config/autostart/io.esp3d.netneighbor.desktop` | Present when **Start NetNeighbor when logging in** is checked in View → Preferences; standard XDG autostart entry. **`Exec=`** appends **`--start-minimized-to-tray`** (tray-only login start) while the **menu** `.desktop` from packaging stays without that flag. Implemented in `utils/session_autostart.py`. |
| `~/.cache/netneighbor/discovery-cache.json` | Volatile discovery cache (`last_seen_overrides`, monitored snapshots metadata, SSDP XML/profile cache). Safe to delete; app rebuilds it. |
| `~/.config/netneighbor/logging.json` | Per-area log levels (`default`, `app`, `ssdp`, `mdns`). Created with defaults on first run if missing. |
| `~/.cache/netneighbor/netneighbor.log` | Log file when file logging is enabled (see `app.py`). |
| `~/.cache/netneighbor/remote_icons/` | On-disk cache for **device-provided** icons fetched from SSDP/mDNS URLs (normalized). Primary file per URL: **`{SHA256(url)}.payload`** raw HTTP body (decoded with GdkPixbuf on load — avoids flaky `savev`). The digest uses a **canonical URL** after rewriting typical LAN hosts (`*.local`, `*.lan`, single-label names) to the **device IP** (stable fetch + hash), then normalizing (lowercased scheme/host, trailing FQDN dot stripped, **default HTTP/HTTPS ports omitted**). Disk lookup also tries **legacy explicit `:80` / `:443`** variants so filenames from older canonicalization rules still resolve. A second lookup tries the **canonical raw URL** (pre-IP rewrite) for older payloads. **HTTPS** to `*.local` / `*.lan` or **RFC1918** hosts uses a relaxed TLS context: certificate verification is disabled (self-signed printer certs) **and** `SECLEVEL=0` is set to allow legacy cipher suites and short keys that older embedded firmware (HP, Canon, Lexmark…) may require. Standard public HTTPS URLs go through the system default SSL context with full verification. Legacy **`.png`** files from older releases are still read if present. Clearing the folder forces a fresh download; if icons look wrong after a firmware change, clear **`remote_icon_index.json`** (see next row) as well. |
| `~/.cache/netneighbor/remote_icon_index.json` | **Host → icon cache index** (version 2 JSON): per **IPv4/IPv6 key** stores **`canonical_url`**, **`payload_sha256`** (same stem as **`remote_icons/{sha256}.payload`**), and **`updated_at`**. Lets the UI load the correct cached icon **on cold start** without waiting for every mDNS TXT variant. Delete this file to drop remembered host→URL mappings (payload files remain until removed manually). |
| `~/.config/netneighbor_icon_packs/` | **User-installed icon packs** (one subfolder per pack). Intentionally outside `~/.config/netneighbor/` so *Reset all application data* does not wipe them. Each subfolder may contain `iconpack.json` (`name`, `owner`, `version`, `license`, `repository` fields; optional) and icon files under `{N}x{N}/` or flat `{N}/` subdirectories. Active pack ID stored in `ui_prefs.json` under `icon_pack`. See `utils/icon_packs.py`. |
| `~/.config/netneighbor/discovery.json` | Discovery toggles + startup refresh schedule. Top-level **`mdns`** / **`ssdp`** with **`enabled`**, **`rules`**, optional **`query`**: SSDP **`interval_seconds`** (periodic M-SEARCH cadence, default 60), **`mx_seconds`** (M-SEARCH **MX** max wait, clamped **5–6**, default 5), **`descriptor_http_min_interval_seconds`** (minimum gap between HTTP GETs for descriptor URLs whose **host is the same IP** (or same hostname if not numeric); avoids duplicate fetches when anticipatory XML and SSDP LOCATION arrive close together; **0** disables; default **5**, max **120**); mDNS **`enumeration_timeout_seconds`** (DNS-SD type scan, **2–3** s, default 2.5), **`enumeration_interval_seconds`** (repeat scan, default 240), **`service_info_timeout_ms`** (`get_service_info`, **2000–3000** ms, default 2500). **`merge.protocol_order`**: ordered protocol **`source`** ids (default **`ssdp`**, **`mdns`**) — earlier = stronger for live-row tie-breaks. **`merge.information_precedence`**: ordered roles (**`user_override`**, **`ssdp_live`**, **`ssdp_profile_cache`**, **`mdns`**) — must list all four exactly once to customize; defaults favour user prefs, then live SSDP, then disk-cache hints on mDNS, then raw mDNS (see `utils/discovery_config.py`). **`startup_refresh_seconds`** at root (comma-separated string or JSON array). |

## Logging

- Root logging is configured in `app.py` (console + optional rotating file under cache dir).
- **`logging.json`**: keys `ssdp` and `mdns` apply both to the protocol module (`discovery.ssdp`, `discovery.mdns`) and to manager-side lines for that source (`discovery.manager.ssdp`, `discovery.manager.mdns`), including “Device added/updated”, **location** resolution (`Location: …`), and **device appearance** (type, bundled icon filename, `user_location`). Set **`app`** to **DEBUG** for **`ui.device_list`** messages (remote vs bundled icon path, GTK theme fallback).
- Special levels **`NONE`**, **`OFF`**, **`DISABLED`**, **`SILENT`** turn off that area (no INFO/DEBUG/WARNING from those loggers).
- **`NETNEIGHBOR_LOG_LEVEL`** still overrides the root/default level when set.
- Other useful loggers: `discovery.manager` (start/stop, publishing at DEBUG), `ui`.

## Systray, window chrome, fullscreen

- **Tray**: `ui/systray.py` — `NetNeighborTray(QSystemTrayIcon)`. Tray menu: **Show window** / **Hide window** / **Quit**. Left-click toggles window visibility. Created in `app_qt.py` when `QSystemTrayIcon.isSystemTrayAvailable()`. On Linux this requires a desktop environment with a system tray (most include one).
- **Icons**: canonical vector logo — **`assets/svg/netneighbor_icon.svg`**. The installed `.deb` copies the SVG into `/usr/share/icons/hicolor/scalable/apps/` as `io.esp3d.netneighbor.svg`, and the tray SVG as `io.esp3d.netneighbor-tray.svg`.
- **Close-to-tray**: `closeEvent` in `ui/main_window.py` calls `event.ignore()` + `self.hide()` when `close_to_tray` pref is `True` and a tray is available; otherwise normal close.
- **Minimize to tray**: `changeEvent` intercepts `WindowStateChange` + `isMinimized()`; schedules `self.hide()` via `QTimer.singleShot(0, …)`.
- **CLI `--start-minimized-to-tray`**: when set and tray is available, `window.show()` is skipped so the app starts invisible.
- **F11 fullscreen**: handled via `keyPressEvent` in `ui/main_window.py`.
- **Open on PCs**: **Open** / double-click uses `resolve_connect_target` — **HTTP(S)** first, then **SMB** / **FTP** / **SSH** / **SFTP** / **Telnet** from merged mDNS or explicit device URLs; type `computer` with no other target opens `smb://` toward the host IP. The resulting URI is opened via `launch_connect_for_uri` (`utils/connect_launcher.py`) — empty per-scheme template in **View → Preferences… → Applications** keeps the system default. If no target exists, **Open** is inactive.

## Icon packs

NetNeighbor supports swappable icon packs for device-type icons. The active pack is selected in **Preferences → General → Icon pack** and stored as `icon_pack` in `ui_prefs.json`.

- **Resolution**: `utils/qt_device_icons.py` (`_bundled_freedesktop_qicon`) checks the active pack first, then falls back to the built-in `assets/icons/netneighbor/`. Supports both `{N}x{N}/` and flat `{N}/` directory naming.
- **User packs**: `~/.config/netneighbor_icon_packs/{pack_id}/` — survives Reset. Add `iconpack.json` for display name; otherwise the directory name is shown.
- **Cache**: changing the pack invalidates the module-level cache (`utils/icon_packs.invalidate_icon_pack_cache`) and `QPixmapCache` so the view rebuilds immediately.

## Bundled device icons (netneighbor)

`assets/icons/netneighbor/` contains PNG fallback icons for ~1 000
[freedesktop.org](https://specifications.freedesktop.org/icon-naming-spec/icon-naming-spec-latest.html)
icon names, supplied at multiple pixel resolutions. The source tree retains **all**
resolutions (~151 MB total) for archival — they are available for future UI changes or
higher-DPI presets without requiring a new source checkout.

Packages ship only the **5 sizes** actually used by the Qt icon-view presets:

| Folder | UI preset | HiDPI equivalent |
|--------|-----------|-----------------|
| `16/` | Small (16 px) | — |
| `32/` | Medium (32 px) | Small @2× |
| `48/` | Large (48 px) | Medium @2× |
| `96/` | Extra large (96 px) | Large @2× |
| `256/` | — | high-DPI launcher / app icon |

All other resolution directories (24, 64, 128, …) are stripped during the packaging
build step (`packaging/linux/build_*.sh`, `packaging/windows/build.ps1`), saving
~143 MB per artifact.

`bundled_freedesktop_png_side_sizes()` in `ui/icons.py` discovers the available sizes
dynamically at runtime by scanning for `{N}x{N}` subdirectories, so trimming the
directory has no effect on the runtime code path — it simply resolves to a smaller set.
User packs that use flat `{N}/` directories are handled by `scan_pack_sizes()` / `find_icon_in_pack()` in `utils/icon_packs.py`.

## Common tasks

- **Rules tuning**: shipped `config/ssdp_rules.json` / `config/mdns_rules.json`; user overlays in **`~/.config/netneighbor/`** — [`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md).
- **Icons / types**: `data/device_types.json` and [`CONTRIBUTING_ICONS.md`](CONTRIBUTING_ICONS.md); optional user merge from `~/.config/netneighbor/device_types.json` ([`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md)). Icons loaded from SSDP/mDNS URLs are persisted under **`~/.cache/netneighbor/remote_icons/`** with a **`remote_icon_index.json`** host map (see table above).
- **Translations**: `locale/*/LC_MESSAGES/netneighbor.po`, compile with `msgfmt`; see [`I18N.md`](I18N.md) for full workflow.

## Discovery extension hooks

- **`DiscoveryManager.register_presence_transition_hook` / `unregister_presence_transition_hook`** (`discovery/manager.py`): optional observers for **`"online"`** / **`"offline"`** transitions per device identity row (not wired in core UI). Callbacks run on the same thread as the discovery update — use `GLib.idle_add` before touching GTK. Failures are logged and do not stop discovery. Planned use: future plugins / alerting. Extension code may import `DiscoveryManager`, `PresenceTransitionHook`, and `PresenceTransitionKind` from the **`discovery`** package (`from discovery import …`).

## Debugging discovery issues

1. Confirm only **one** app instance (SSDP bind + multicast).
2. Check firewall allows UDP 1900 inbound / multicast.
3. Inspect SSDP details in the UI for `LOCATION`, `USN`, XML presence.
4. Compare logs for “XML fetched” vs failures (timeouts, parse errors).
5. **Location looks like a URL / `description.xml`**: should no longer happen; if prefs were polluted earlier, open **Location presets** or edit **`ui_prefs.json`** — invalid URL-like presets are stripped on load. **`utils/location_label.py`** documents the rejection rules.
6. **Wrong or delayed device icon**: confirm **`remote_icons/*.payload`** and **`remote_icon_index.json`** together; bump **`mdns`** / **`app`** to **DEBUG** to trace aggregate + icon load path.
7. **Device details (first tab)**: duplicate **Last seen** rows are collapsed to the latest timestamp; bundled WSD/wsdd/NMB-only rows omit legacy **Discovery** / **WSD XAddrs** / **NetBIOS registration** lines (overview + protocol fields stay minimal). **IPv6 (link-local)** in the overview only appears when a `fe80::` address is inferred from device IP or WSD/wsdd payloads — many Windows PCs only advertise IPv4 in WSD URLs, so an empty IPv6 line there is expected.

## Changelog / release notes

Archived in [`archive/CHANGELOG.md`](archive/CHANGELOG.md) (up to **0.8.0**). New release notes go in the repository changelog or release tags.

## Suggested additional docs (optional)

| Document | When to add |
|----------|-------------|
| `TESTING.md` | Once you have automated tests or a repeatable manual checklist per release. |
| `PACKAGING.md` | When AppImage / `.deb` pipeline is scripted (commands, deps, smoke test). |
| `TROUBLESHOOTING.md` | User-facing FAQ distilled from GitHub issues (short). |

Architecture Decision Records (ADR): optional short files under `docs/adr/` for major choices
(why GTK3, why in-memory store first, etc.) if the team grows.

## Release checklist (minimal)

- [ ] **`VERSION`** bumped.
- [ ] `README.md` and `USER_DOCUMENTATION.md` aligned with new behavior.
- [ ] `docs/MAINTENANCE.md`, `docs/PACKAGING.md`, and `docs/COMMUNITY_OVERRIDES.md` updated if behavior or deps changed.
- [ ] `docs/TODO.md` updated (move done items, add new known issues).
- [ ] Translation catalogs merged (`msgmerge`) and `.mo` files compiled.
- [ ] Smoke run: start app, discovery, details, tray hide/show, **CLI** `--start-minimized-to-tray`, optional autostart toggle, monitored device restart.
- [ ] Test Open / double-click with Override and Additional device commands.
- [ ] Packaging smoke: `./packaging/linux/build_deb.sh`, install in VM, verify icons + `Recommends` (tray + `nmblookup`).
