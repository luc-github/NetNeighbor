# UI architecture (GTK 3)

## Goals and choices

- **Native Linux desktop**: GTK 3 + PyGObject, no embedded browser for the main list.
- **Responsive discovery**: protocol threads must not block the GTK main loop; updates are
  marshaled with `GLib.idle_add` from `MainWindow._on_devices_updated`.
- **Two views**: list (`Gtk.TreeView`) and icon grid (`Gtk.FlowBox` sections) — user preference persisted.
- **Fullscreen**: **F11** toggles `Gtk.Window` fullscreen (accel group on the main window; works while child widgets have focus).
- **Tray-capable session**: optional **close-to-tray**, **start minimized to tray**, and **XDG autostart** — see `MainWindow` + `TrayIndicator` + `utils/session_autostart.py`.
- **Discoverability**: sidebar grouped by type or location (depending on arrange mode), context menus for monitor / type / rename / location / icon details.

Dialog chrome: **`prepare_gtk_dialog`** in `utils/gtk_dialog.py` (disable header-bar dialogs where needed).

## Module layout

| Module | Responsibility |
|--------|----------------|
| `app.py` | `Gtk.Application`, single-instance lock, activation socket, `DiscoveryManager`, `MainWindow` lifecycle, optional demo mode. Parses **`--start-minimized-to-tray`** (and demo flags) via `parse_known_args` before `run()`; forwards remaining argv for GTK plumbing. |
| `ui/main_window.py` | Menu bar, paned layout (sidebar + content), discovery listener wiring, notifications mode, UI prefs load/save, notification history (session), sidebar counts. When **close-to-tray** works, installs a **Gtk.HeaderBar** (**Maximize + Close**; minimize via tray menu). **F11** fullscreen accel. Tray creation on idle; skip initial present when starting minimized-to-tray; **View → Quit** calls `application.quit()`. |
| `ui/tray_indicator.py` | Ayatana **AppIndicator** / **AppIndicator3**, else **Gtk.StatusIcon**; tray menu **Open** / **Minimize to tray** / **Quit**; resolves icon names (bundled hicolor **SVG** under `assets/icons/`). |
| `utils/session_autostart.py` | Writes or removes `~/.config/autostart/io.esp3d.netneighbor.desktop` from the **Start when logging in** preference. |
| `ui/device_list.py` | `DeviceList`: bundles devices by `(ip, port)` for display (mDNS + SSDP on same host), list store, icon tiles, context menus, details entry points. **Open** (`open_url`) is disabled for bundled rows whose primary **type** is **`computer`** (WSD metadata URL not opened in browser). |
| `ui/device_details.py` | Dialog: **Overview** summary + protocol fields; optional raw XML, SSDP services table, mDNS service expanders **Services** tab, icon tab. Multiline values are not rendered as `Gtk.LinkButton`. |
| `utils/details_payload.py` | Pure builders for SSDP / mDNS / WSD-family detail rows; merges duplicate **Last seen** to the latest; WSD-only supplementary rows are minimal (no Discovery / XAddrs / NetBIOS registration lines). |
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
represents one host/device entry. The **primary** row follows **`merge.information_precedence`** in `discovery.json` across live protocol rows for that endpoint (not “mDNS always wins”).

## Threading and notifications

- Desktop notifications are triggered from transition logic on the GTK idle callback path;
  sending is done in a **background thread** so a stalled notification daemon cannot freeze the UI.
- **Notification history** (menu → Tools → Notifications history) stores session-only rows (timestamp, device name, status).

## Preferences (persisted)

Stored in `ui_prefs.json`, including view mode, sidebar position, notification mode, category/location selection,
type overrides, monitored flags, last-seen overrides, icon appearance overrides (system/provided/custom + custom icon choice), **monitored device
snapshots** used to show greyed monitored entries after restart before rediscovery, and tray/session flags:
**`close_to_tray`**, **`start_minimized_to_tray`**, **`start_at_login`**.  
See [`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md) for a short key reference; [`MAINTENANCE.md`](MAINTENANCE.md) for paths and tray dependencies.

## Styling

`DeviceList` installs a small CSS provider for offline monitored tint (`.offline-device-monitored`).

## Internationalization

GNU gettext; catalogs under `locale/`. UI strings use `_()` where wired.  
See [`I18N.md`](I18N.md) for add/update workflow.

## See also

- [`MAINTENANCE.md`](MAINTENANCE.md) — paths, tray, packaging-related runtime deps, troubleshooting.  
- [`PACKAGING.md`](PACKAGING.md) — `.deb` layout and **Recommends** (AppIndicator, `nmblookup`).  
- [`NetNeighbor_Brief_v1.4.md`](NetNeighbor_Brief_v1.4.md) — product constraints (e.g. GLib idle requirement).
