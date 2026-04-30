# Maintenance and follow-up

This complements [`README.md`](../README.md) and the [developer index](README.md) for anyone
keeping the project healthy between releases.

## Configuration paths

| Path | Content |
|------|---------|
| `~/.config/netneighbor/ui_prefs.json` | UI state, overrides, monitored snapshots (not a full device DB). |
| `~/.config/netneighbor/logging.json` | Per-area log levels (`default`, `app`, `ssdp`, `mdns`). Created with defaults on first run if missing. |
| `~/.cache/netneighbor/netneighbor.log` | Log file when file logging is enabled (see `app.py`). |

## Logging

- Root logging is configured in `app.py` (console + optional rotating file under cache dir).
- **`logging.json`**: keys `ssdp` and `mdns` apply both to the protocol module (`discovery.ssdp`, `discovery.mdns`) and to manager-side lines for that source (`discovery.manager.ssdp`, `discovery.manager.mdns`), including “Device added/updated”.
- Special levels **`NONE`**, **`OFF`**, **`DISABLED`**, **`SILENT`** turn off that area (no INFO/DEBUG/WARNING from those loggers).
- **`NETNEIGHBOR_LOG_LEVEL`** still overrides the root/default level when set.
- Other useful loggers: `discovery.manager` (start/stop, publishing at DEBUG), `ui`.

## Common tasks

- **Rules tuning**: edit `config/ssdp_rules.json` — see [`SSDP_RULES_JSON.md`](SSDP_RULES_JSON.md).
- **Icons / types**: `data/device_types.json` and [`CONTRIBUTING_ICONS.md`](CONTRIBUTING_ICONS.md).
- **Translations**: `locale/*/LC_MESSAGES/netneighbor.po`, compile with `msgfmt`.

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
