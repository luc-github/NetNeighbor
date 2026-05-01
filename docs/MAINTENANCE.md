# Maintenance and follow-up

This complements [`README.md`](../README.md) and the [developer index](README.md) for anyone
keeping the project healthy between releases.

## Configuration paths

| Path | Content |
|------|---------|
| `~/.config/netneighbor/ui_prefs.json` | UI state, overrides, monitored snapshots (not a full device DB). |
| `~/.config/netneighbor/logging.json` | Per-area log levels (`default`, `app`, `ssdp`, `mdns`). Created with defaults on first run if missing. |
| `~/.cache/netneighbor/netneighbor.log` | Log file when file logging is enabled (see `app.py`). |
| `~/.cache/netneighbor/remote_icons/` | On-disk cache for **device-provided** icons fetched from SSDP/mDNS URLs (normalized). Primary file per URL: **`{SHA256(url)}.payload`** raw HTTP body (decoded with GdkPixbuf on load — avoids flaky `savev`). The digest uses a **canonical URL** after rewriting typical LAN hosts (`*.local`, `*.lan`, single-label names) to the **device IP** (stable fetch + hash), then normalizing (lowercased scheme/host, trailing FQDN dot stripped). A second lookup tries the **canonical raw URL** (pre-IP rewrite) for older payloads. **HTTPS** to `*.local` / `*.lan` or **RFC1918** hosts uses a relaxed TLS verify context (typical self-signed printer certs). Legacy **`.png`** files from older releases are still read if present. Clearing the folder forces a fresh download. |

## Logging

- Root logging is configured in `app.py` (console + optional rotating file under cache dir).
- **`logging.json`**: keys `ssdp` and `mdns` apply both to the protocol module (`discovery.ssdp`, `discovery.mdns`) and to manager-side lines for that source (`discovery.manager.ssdp`, `discovery.manager.mdns`), including “Device added/updated”.
- Special levels **`NONE`**, **`OFF`**, **`DISABLED`**, **`SILENT`** turn off that area (no INFO/DEBUG/WARNING from those loggers).
- **`NETNEIGHBOR_LOG_LEVEL`** still overrides the root/default level when set.
- Other useful loggers: `discovery.manager` (start/stop, publishing at DEBUG), `ui`.

## Common tasks

- **Rules tuning**: shipped `config/ssdp_rules.json` / `config/mdns_rules.json`; user overlays in **`~/.config/netneighbor/`** — [`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md).
- **Icons / types**: `data/device_types.json` and [`CONTRIBUTING_ICONS.md`](CONTRIBUTING_ICONS.md); optional user merge from `~/.config/netneighbor/device_types.json` ([`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md)). Icons loaded from SSDP/mDNS URLs are persisted under **`~/.cache/netneighbor/remote_icons/`** (see table above).
- **Translations**: `locale/*/LC_MESSAGES/netneighbor.po`, compile with `msgfmt`; see [`I18N.md`](I18N.md) for full workflow.

## Discovery extension hooks

- **`DiscoveryManager.register_presence_transition_hook` / `unregister_presence_transition_hook`** (`discovery/manager.py`): optional observers for **`"online"`** / **`"offline"`** transitions per device identity row (not wired in core UI). Callbacks run on the same thread as the discovery update — use `GLib.idle_add` before touching GTK. Failures are logged and do not stop discovery. Planned use: future plugins / alerting (see [`ROADMAP.md`](ROADMAP.md) « Future — plugins »). Extension code may import `DiscoveryManager`, `PresenceTransitionHook`, and `PresenceTransitionKind` from the **`discovery`** package (`from discovery import …`).

## Debugging discovery issues

1. Confirm only **one** app instance (SSDP bind + multicast).
2. Check firewall allows UDP 1900 inbound / multicast.
3. Inspect SSDP details in the UI for `LOCATION`, `USN`, XML presence.
4. Compare logs for “XML fetched” vs failures (timeouts, parse errors).

## Suggested additional docs (optional)

| Document | When to add |
|----------|-------------|
| `TESTING.md` | Once you have automated tests or a repeatable manual checklist per release. |
| `PACKAGING.md` | When AppImage / `.deb` pipeline is scripted (commands, deps, smoke test). |
| `TROUBLESHOOTING.md` | User-facing FAQ distilled from GitHub issues (short). |
| `CHANGELOG.md` | Keep a human-readable history if releases become frequent. |

Architecture Decision Records (ADR): optional short files under `docs/adr/` for major choices
(why GTK3, why in-memory store first, etc.) if the team grows.

## Release checklist (minimal)

- [ ] `README.md` and brief aligned with behavior.
- [ ] `docs/ROADMAP.md` updated.
- [ ] Smoke run: start app, discovery, open details, restart with monitored device.
