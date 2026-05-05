# TODO

Known items and ideas for future work. No priority order.

## Translations

- Fill in `.po` files for: Italian, Spanish, German, Dutch, Traditional Chinese (zh_TW),
  Simplified Chinese (zh_CN), Japanese
- French catalog still has ~150 untranslated strings
- Compile `.mo` files and test layout (especially CJK line wrapping)

## Packaging

- AppImage packaging (optional, tracked in `docs/PACKAGING.md`)

## Discovery

- SFTP service detection via mDNS (`_sftp-ssh._tcp`)
- Optional WSD multicast interface selection (multi-homed hosts)
- NetBIOS: improve when `nmblookup` is not installed (show a hint in UI)

## UI
- Context menu "Open" submenu: keyboard navigation improvement
- Per-device command label shown in tooltip when hovering a device tile
- Notifications history: persist across sessions (currently session-only)

## Testing

- Add a repeatable manual smoke-test checklist per release (see `MAINTENANCE.md`)
- Unit tests for `_normalize_command_list`, `resolve_connect_target` priority logic,
  and `resolve_all_connect_targets` label generation
