> **ARCHIVED — GTK 1.x only.** This document describes the original GTK 3 / PyGObject UI layer
> (NetNeighbor ≤ 1.x). The current UI is PySide6/Qt — see [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md)
> and [`ROADMAP_QT_2_0.md`](ROADMAP_QT_2_0.md) for the 2.0 architecture.

# UI architecture (GTK 3, archived)

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
| `ui/device_list.py` | `DeviceList`: bundles devices by `(ip, port)` for display (mDNS + SSDP on same host), list store, icon tiles, context menus, details entry points. **Open** / double-click resolves **`resolve_connect_target`** (`utils/double_click_open.py`), then **`launch_connect_for_uri`** (`utils/connect_launcher.py`) using **`connect_command_templates`** from **Tools → External applications…**. When ≥ 2 targets exist, right-click shows **Open ▶** submenu (via **`resolve_all_connect_targets`**) with labelled entries. **Run custom command** uses **`custom_command_template`** (`utils/custom_command.py`). |
| `ui/device_details.py` | Dialog: **Overview** summary + protocol fields; **Options** tab for per-device commands (dynamic list — scheme, mode, label, IP, port); optional raw XML, SSDP services table, mDNS service expanders **Services** tab, icon tab. Multiline values are not rendered as `Gtk.LinkButton`. |
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
See [`COMMUNITY_OVERRIDES.md`](../contributing/COMMUNITY_OVERRIDES.md) for a short key reference; [`MAINTENANCE.md`](../operations/MAINTENANCE.md) for paths and tray dependencies.

## Styling

`DeviceList` installs a small CSS provider for offline monitored tint (`.offline-device-monitored`).

## Internationalization

GNU gettext; catalogs under `locale/`. UI strings use `_()` where wired.  
See [`I18N.md`](../contributing/I18N.md) for add/update workflow.

## Per-device command model

The **Options** tab in `device_details.py` manages a list of `dict` entries:

```python
[
    {"scheme": "http",  "ip": "",             "port": 0,  "mode": "override",    "label": ""},
    {"scheme": "ssh",   "ip": "192.168.1.10", "port": 22, "mode": "additional",  "label": "Admin"},
]
```

- Stored in `ui_prefs.json` under `device_commands` (keyed by device identity).
- Validated and normalized by `_normalize_command_list()` in `discovery/manager.py`.
- Applied to each `Device` on every pipeline run via `_apply_device_commands`.
- Read by `resolve_connect_target` / `resolve_all_connect_targets` in `utils/double_click_open.py`.

## Title bar and tray

When **close-to-tray** is active the window uses a `Gtk.HeaderBar` set via `set_titlebar()`.
This call must happen **before** `show_all()` — calling it afterwards loses WM decorations on
Muffin/Cinnamon. `_ensure_tray()` and `_apply_tray_window_decorations()` are therefore invoked
in `__init__` before `show_all()`.

## See also

- [`BACKEND_ARCHITECTURE.md`](../reference/BACKEND_ARCHITECTURE.md) — DiscoveryManager, override system, device commands.
- [`MAINTENANCE.md`](../operations/MAINTENANCE.md) — paths, tray, packaging-related runtime deps, troubleshooting.
- [`PACKAGING.md`](../operations/PACKAGING.md) — `.deb` layout and **Recommends** (AppIndicator, `nmblookup`).
