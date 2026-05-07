# TODO

Known items and ideas for future work. No priority order.

## Discovery

- SFTP service detection via mDNS (`_sftp-ssh._tcp`)
- Optional WSD multicast interface selection (multi-homed hosts)
- NetBIOS: show a hint in the UI when `nmblookup` is not installed
- **Performance** — cache pre-population at startup (emit cached SSDP devices before
  live discovery completes, so the window is populated in < 100 ms)
- **Performance** — NetBIOS directed probes: run in parallel with `ThreadPoolExecutor`
  instead of sequential `nmblookup -A` calls (see `docs/MAINTENANCE.md`)
- **Performance** — SSDP XML fetches: per-host lock instead of single global lock
  (allows concurrent descriptor fetches at startup)
- **Performance** — wsdd_client: remove hardcoded 0.35 s sleep between probe and list

## UI

- Context menu "Open" submenu: keyboard navigation improvement
- Per-device command label shown in tooltip when hovering a device tile
- Notifications history: persist across sessions (currently session-only)

## Testing

- Add a repeatable manual smoke-test checklist per release (see `MAINTENANCE.md`)
- Unit tests for `_normalize_command_list`, `resolve_connect_target` priority logic,
  and `resolve_all_connect_targets` label generation
