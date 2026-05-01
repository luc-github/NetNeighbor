# UI architecture (GTK 3)

## Goals and choices

- **Native Linux desktop**: GTK 3 + PyGObject, no embedded browser for the main list.
- **Responsive discovery**: protocol threads must not block the GTK main loop; updates are
  marshaled with `GLib.idle_add` from `MainWindow._on_devices_updated`.
- **Two views**: list (`Gtk.TreeView`) and icon grid (`Gtk.FlowBox` sections) — user preference persisted.
- **Discoverability**: sidebar grouped by type or location (depending on arrange mode), context menus for monitor / type / rename / location / icon details.

## Module layout

| Module | Responsibility |
|--------|----------------|
| `app.py` | `Gtk.Application`, single-instance lock, activation socket, `DiscoveryManager`, `MainWindow` lifecycle, optional demo mode. |
| `ui/main_window.py` | Menu bar, paned layout (sidebar + content), discovery listener wiring, notifications mode, UI prefs load/save, notification history (session), sidebar counts. |
| `ui/device_list.py` | `DeviceList`: bundles devices by `(ip, port)` for display (mDNS + SSDP on same host), list store, icon tiles, context menus, details entry points. |
| `ui/device_details.py` | Dialog for structured fields, optional raw XML / TXT. |
| `utils/details_payload.py` | Pure builders for SSDP and mDNS detail panes from `Device`; mDNS services + TXT ship as structured sections with expanders in the dialog. |
| `utils/ui_prefs.py` | JSON read/write under `~/.config/netneighbor/ui_prefs.json`. |

## Data flow (UI)

```
DiscoveryManager._notify()
    → listener(devices) registered in MainWindow
    → GLib.idle_add(_update_ui_devices)
    → _notify_device_transitions (desktop notifications, optional)
    → _rebuild_sidebar
    → DeviceList.set_devices(devices)
```

`DeviceList.set_devices` rebuilds filtered bundles and both views.

## Bundling and display identity

`_DeviceBundle` groups multiple `Device` rows that share the same `(ip, port)` so one tile
represents one host/device entry. The “primary” row chooses mDNS first if present for labeling.

## Threading and notifications

- Desktop notifications are triggered from transition logic on the GTK idle callback path;
  sending is done in a **background thread** so a stalled notification daemon cannot freeze the UI.
- **Notification history** (menu → Tools → Notifications history) stores session-only rows (timestamp, device name, status).

## Preferences (persisted)

Stored in `ui_prefs.json`, including view mode, sidebar position, notification mode, category/location selection,
type overrides, monitored flags, last-seen overrides, icon appearance overrides (system/provided/custom + custom icon choice), and **monitored device
snapshots** used to show greyed monitored entries after restart before rediscovery.

## Styling

`DeviceList` installs a small CSS provider for offline monitored tint (`.offline-device-monitored`).

## Internationalization

GNU gettext; catalogs under `locale/`. UI strings use `_()` where wired.  
See [`I18N.md`](I18N.md) for add/update workflow.

## See also

- [`MAINTENANCE.md`](MAINTENANCE.md) — paths and troubleshooting.  
- [`NetNeighbor_Brief_v1.4.md`](NetNeighbor_Brief_v1.4.md) — product constraints (e.g. GLib idle requirement).
