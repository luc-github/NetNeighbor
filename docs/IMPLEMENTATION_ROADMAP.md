# NetNeighbor — Implementation Roadmap

Last updated: 2026-05-18

This document tracks planned and completed improvements, grouped by theme.
Each section lists the motivation, the files involved, the acceptance criteria,
and the implementation status.

---

## Status legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Done — merged and tested |
| 🔄 | In progress |
| 📋 | Planned — design agreed |
| 💡 | Idea — not yet scoped |

---

## Theme A — Startup performance

**Context:** The application was taking 3+ seconds before showing the window, then
another 3–4 seconds before devices appeared. The UI thread was blocked by heavy imports
and by `manager.start()` running synchronously.

### A-1 Lazy imports for wsdiscovery and zeroconf ✅

- **Files:** `discovery/wsd.py`, `discovery/mdns.py`
- **Change:** Moved `import wsdiscovery` / `import zeroconf` inside `start()` via
  `_ensure_wsd_libs()` / `_ensure_zeroconf_libs()`. Both are now imported on first use
  rather than at module load time.
- **Gain:** ~500 ms moved after window display.

### A-2 Single-instance timeout reduction ✅

- **File:** `utils/qt_single_instance.py`
- **Change:** `waitForConnected` reduced from 800 ms to 200 ms.

### A-3 Discovery start in background thread ✅

- **File:** `app_qt.py`
- **Change:** `manager.start()` + `manager.refresh()` now run in a `threading.Thread`
  launched via `QTimer.singleShot(0, ...)` so the Qt event loop starts clean and the
  window (and overlay) paint before any blocking work begins.

### A-4 Scanning overlay ✅

- **File:** `ui_qt/main_window.py` — class `_ScanningOverlay`
- **Change:** Centered translucent overlay with animated spinner and "Start Scanning…"
  text, shown while waiting for the first devices. Animation runs in a background thread
  emitting a `Signal(QueuedConnection)` at 11 Hz to stay independent of main-thread load.
  Auto-dismissed when the first device appears, or after 12 s.

---

## Theme B — System tray and session autostart

**Context:** Users want the application to live in the system tray, minimize to the
tray instead of the taskbar, and optionally start automatically at session login.

### B-1 System tray icon ✅

- **File:** `ui_qt/systray.py` (new), `ui_qt/__init__.py`, `app_qt.py`
- **Change:** `NetNeighborTray(QSystemTrayIcon)` with Show/Hide + Quit context menu.
  Left-click toggles window visibility. Label stays in sync with window state.
  Created in `app_qt.py` when `QSystemTrayIcon.isSystemTrayAvailable()`.

### B-2 Minimize to tray instead of taskbar ✅

- **File:** `ui_qt/main_window.py` — `changeEvent`
- **Change:** Intercepts `WindowStateChange` when `isMinimized()`. Schedules
  `self.hide()` via `QTimer.singleShot(0, ...)` to avoid in-transition side effects.
  `bring_to_front()` uses `showNormal()` to clear the minimized state on restore.

### B-3 Close to tray ✅

- **File:** `ui_qt/main_window.py` — `closeEvent`
- **Change:** If `close_to_tray` preference is `True` (default) and a tray is available,
  `closeEvent` calls `event.ignore()` + `self.hide()` instead of accepting the close.

### B-4 Session autostart ✅

- **File:** `utils/session_autostart.py`
- **Change:** Extended with Windows registry support (`HKCU\...\Run`). Unified public
  API — `autostart_enabled_on_disk()` and `apply_autostart_pref()` — dispatches by
  platform (XDG `.desktop` on Linux/macOS, `winreg` on Windows). Launch command always
  includes `--start-minimized-to-tray`.

### B-5 Start minimized to tray ✅

- **File:** `app_qt.py`
- **Change:** `--start-minimized-to-tray` CLI argument. When set and tray is available,
  `window.show()` is skipped so the application starts invisible.

---

## Theme C — UI correctness fixes

### C-1 Icon view empty after restore from tray ✅

- **File:** `ui_qt/main_window.py` — `_rebuild_grouped_icon_page`
- **Root cause:** `was_page_visible = page.isVisible()` returned `False` while the
  window was hidden. The page was then explicitly hidden via `setVisible(False)`,
  preventing Qt from showing it when the window was restored.
- **Fix:** Changed to `was_page_visible = not page.isHidden()` which tests the
  explicit-hide flag rather than effective screen visibility.

### C-2 Sidebar loses all types when a filter is active ✅

- **File:** `ui_qt/main_window.py` — `_rebuild_sidebar`
- **Root cause:** Sidebar was built from `_filtered_bundles()` which already applied
  `_selected_category`. Clicking "by Type" (already selected) triggered a rebuild
  where only the filtered type remained visible; other types were dropped.
- **Fix:** Sidebar is now built from all non-hidden bundles (`self._bundles` minus
  hidden), independent of the active filter.

### C-3 Empty line at top of sidebar in list mode ✅

- **File:** `ui_qt/main_window.py` — `_sync_sidebar_top_spacer`
- **Root cause:** A spacer equal to the table header height was added to align the
  sidebar list with the table body. It appeared as a blank row.
- **Fix:** Removed the list-mode special case; spacer is always 0.

### C-4 Location auto-add not implemented ✅

- **File:** `ui_qt/main_window.py`
- **Root cause:** The `auto_add_discovered_locations` preference was saved but never
  read to actually populate `_location_options`.
- **Fix:** `_maybe_auto_add_discovered_locations()` is called after each device
  snapshot. If the pref is enabled, it collects `user_location` from all bundles,
  merges into `_location_options` via `normalize_location_options`, and persists.

### C-5 Current device location missing from context menu ✅

- **File:** `ui_qt/main_window.py` — `_show_device_context_menu`
- **Root cause:** If `_location_options` is empty (fresh config), the location menu
  was empty even for devices that already had a location assigned.
- **Fix:** The device's current `bundle_location_label` is injected into `loc_opts`
  before passing to the menu, without modifying the global `_location_options`.

---

## Theme D — UI responsiveness (refresh / idle management)

**Context:** Periodic discovery events were triggering full UI rebuilds even with no
visible changes. Context menus disappeared, hover states jumped, scroll position reset.

### D-1 Active popup guard ✅

- **File:** `ui_qt/main_window.py` — `_flush_pending_devices`
- **Change:** If `QApplication.activePopupWidget()` is not None (a context menu is
  open), the flush is rescheduled 400 ms later. Prevents widget rebuilds from closing
  an active menu.

### D-2 User activity guard ✅

- **File:** `ui_qt/main_window.py` — `_UserActivityGuard`, `_flush_pending_devices`
- **Change:** Application-level event filter tracks last mouse/keyboard timestamp.
  Flush is deferred by 250 ms if `idle_ms() < 200`. Prevents hover-state jumps while
  the user is navigating.

### D-3 Fingerprint early bail ✅

- **File:** `ui_qt/main_window.py` — `_apply_devices_snapshot`,
  `_device_snapshot_ui_fingerprint`
- **Change:** `user_location` added to the device fingerprint. If the device fingerprint
  and the precedence list are unchanged and the view has already been rendered
  (`_last_device_view_sig is not None`), the snapshot is skipped entirely — no bundle
  build, no sidebar check, no view refresh timer.

### D-4 Scroll position preserved ✅

- **Files:** `ui_qt/main_window.py` — `_fill_table`, `_fill_flat_icon_list`
- **Change:** Vertical scroll position is saved before and restored after each rebuild.
  For the flat icon list, restoration is deferred via `QTimer.singleShot(0, ...)` to
  allow the relayout pass to complete first.

---

## Theme E — Device lifetime and cache validity

**Context:** Devices that silently disappear can remain "online" for minutes. The
disk cache accumulates stale entries indefinitely. There is no active validation of
cached devices at startup, and no visual distinction between "confirmed live" and
"last seen N hours ago".

### E-1 Cache purge and `last_seen` timestamp ✅

- **Files:** `utils/discovery_cache.py`
- **Change:**
  - Add `last_seen` (ISO-8601 UTC) field to each cache entry, updated on every write.
  - At load time: skip entries older than a configurable threshold (default 48 h).
    Currently entries > 24 h are skipped, but stale entries are not removed from disk.
  - At save time: remove entries that have exceeded the threshold instead of only
    merging new data.
- **Acceptance:** After 48 h without seeing a device, it does not appear at next
  startup. Cache file does not grow unboundedly.

### E-2 "Last seen" indicator in UI ✅

- **Files:** `ui_qt/main_window.py` (tooltip), `ui_qt/icon_tile_delegate.py` (optional
  visual dimming)
- **Change:**
  - Device tooltip includes "Last seen: 3 h ago" when `last_seen` is available and the
    device has not been confirmed live in this session.
  - Optionally: icon slightly desaturated / opacity reduced for cache-only devices not
    yet confirmed live.
- **Acceptance:** User can distinguish a device seen 30 s ago from one seen 23 h ago.

### E-3 NetBIOS TTL at refresh ✅

- **File:** `discovery/netbios.py`
- **Change:** `NetbiosDiscovery` now tracks `_nmb_known` (last payload per IP) and
  `_nmb_miss_counts` (consecutive sweeps without a response). After each `_run_once`,
  hosts absent for ≥ 2 consecutive sweeps receive an offline event. Transient failures
  (timeout / OSError) return early without incrementing miss counts. `stop()` clears
  both dicts.
- **Acceptance:** A NetBIOS device that leaves the network is marked offline within
  2 refresh cycles (default: ~6 min with 3-minute interval).

### E-4 WSD / wsdd offline detection ✅

- **Files:** `discovery/wsd.py`, `discovery/wsdd_client.py`
- **Change:**
  - **WSD:** `WSDiscovery._run_once` now calls `eng.clearRemoteServices()` before each
    probe sweep, so `_remoteServices` only contains devices that responded in the current
    cycle. TTL tracking via `_wsd_known_eprs` / `_wsd_miss_counts` emits offline after
    2 missed sweeps. Transient probe failures return early without penalising known hosts.
  - **wsdd:** `WsddSocketDiscovery._poll_once` collects all list-response payloads first,
    then applies TTL via `_wsdd_known` / `_wsdd_miss_counts` (grace: 2 polls).
    Socket failures return early without penalising known hosts.
- **Acceptance:** A WSD/wsdd device that disappears from the network is marked offline
  within 2 sweep/poll cycles.

### E-5 TCP probe validation for cached devices ✅

- **Files:** `discovery/manager.py`, `utils/tcp_probe.py` (new)
- **Change:**
  - `utils/tcp_probe.py`: `probe_tcp(ip, port, timeout_s)` performs a single TCP
    connect. `probe_devices_background(targets, on_result, ...)` runs all targets
    concurrently in a `ThreadPoolExecutor` inside a daemon thread, calling
    `on_result(ip, port, reachable)` for each.
  - `manager.start()` calls `_start_tcp_probes_for_cached_devices()` right after
    `_emit_cached_devices()`. It collects all online cached endpoints with a valid port
    (skips loopback and link-local IPv6), then launches a background probe (max 8
    concurrent, 1.5 s timeout).
  - On success: `last_seen` updated to now on all matching devices.
  - On failure: `device.online = False` set directly, then `_notify()` called.
    Guard: if a live protocol has already confirmed the device online since probe start
    (`last_seen > probe_start`), the offline flip is skipped.
- **Acceptance:** Cached devices that are unreachable are marked offline within ~5 s
  of startup, before the full protocol discovery completes (~15–30 s).

---

## Theme F — Discovery enhancements 💡

*Items from `docs/TODO.md` and outstanding ideas — not yet scheduled.*

- SFTP service detection via mDNS (`_sftp-ssh._tcp`)
- NetBIOS: hint in UI when `nmblookup` is not installed
- SSDP XML fetches: per-host lock for concurrent descriptor fetches
- wsdd_client: remove hardcoded 0.35 s sleep between probe and list
- NetBIOS directed probes: parallel `ThreadPoolExecutor`

---

## Theme G — UX polish 💡

- Context menu "Open" submenu: keyboard navigation improvement
- Per-device command label in tile tooltip
- Notifications history: persist across sessions
- Preferences: expose cache TTL threshold (currently hardcoded 48 h)

---

## Theme H — Translation review and updates ✅

**Context:** The application was ported from GTK to PySide6 (version 2.0). Several new
UI strings were added (system tray, scanning overlay, preferences, context menus,
autostart). All existing `.po` files needed to be audited for completeness and accuracy
against the current source strings.

8 languages are currently shipped: `fr`, `es`, `de`, `it`, `nl`, `ja`, `zh_CN`, `zh_TW`.

### H-1 Extract updated source strings ✅

- **Tool:** `tools/extract_new_strings.py` — AST-based extraction via `ast.parse()`,
  walks all `Call(func=Name(id='_'))` nodes with `Constant` string arguments.
- **Files:** `ui_qt/**/*.py`, `discovery/*.py`, `utils/*.py` → `locale/netneighbor.pot`
- **Result:** 74 new strings found not present in the old `.pot` (which referenced GTK paths).

### H-2 Audit and update each `.po` file ✅

- **Tool:** `tools/update_translations.py` — appends new `msgid`/`msgstr` pairs to each `.po`.
- **Files:** `locale/*/LC_MESSAGES/netneighbor.po` — all 8 languages updated.
- **New strings include:** tray menu (`Show window`, `Hide window`, `Show/Hide sidebar`),
  overlay (`Start Scanning…`), last-seen tooltips (`Last seen: {n} min/h/d ago`),
  preferences sections (`Theme`, `Light`, `Dark`, `General`, `Session`, `Notifications`,
  `Sidebar`, `Types`, `Locations`, `Icons size`, `Small`/`Medium`/`Large`/`Extra large`),
  context menu (`Hide device`), commands prefs, field mapping, about dialog, etc.
- **All 74 strings fully translated** in all 8 languages.

### H-3 Recompile `.mo` files and smoke-test ✅

- **Tool:** `babel` (`read_po` + `write_mo`) — compiled directly from Python.
- **Result:** All 8 `.mo` files recompiled and verified via `gettext.translation()`.
- **Spot-check passed:** `{} devices`, `Online`, `Offline`, `Start Scanning…` return
  correct translated strings in every locale.

---

## Theme I — Packaging 📋

**Context:** The packaging scripts under `packaging/` were written for the GTK 1.x
version and reference `app.py` (which no longer exists) and GTK runtime dependencies.
All three platform targets need to be updated for PySide6 / `main.py`.

Test targets: **Windows** and **Linux**. macOS packaging will be scripted but cannot
be validated without a macOS test machine.

### I-1 Audit and update existing packaging scripts 📋

- **Files:** `packaging/linux/build_deb.sh`, `packaging/linux/build_tarball.sh`,
  `packaging/linux/build_appimage.sh`, `packaging/windows/build.ps1`,
  `packaging/macos/build_app.sh`, `docs/PACKAGING.md`
- **Change:**
  - Replace all references to `app.py` with `main.py` (unified entry point).
  - Remove GTK runtime dependencies (`python3-gi`, `python3-gi-cairo`, `gir1.2-gtk-3.0`,
    AppIndicator bindings) from `.deb` control file and install docs.
  - Add PySide6 runtime dependency notes.
  - Update `docs/PACKAGING.md` to reflect 2.0 reality.

### I-2 Linux packaging (`.deb` + `AppImage` + tarball) 📋

- **Files:** `packaging/linux/`
- **Change:**
  - `.deb`: update `Depends` control field — remove GTK, keep `python3`, `python3-pip`,
    `samba-common-bin` (NetBIOS); add post-install `pip install -r requirements-qt.txt`
    or vendor PySide6 wheels inside the package.
  - AppImage: bundle PySide6 + all Python dependencies inside the image so it runs
    without any system Python packages.
  - Tarball: add a `run.sh` that creates a venv and installs `requirements-qt.txt` on
    first run.
  - System tray: verify `libxcb-cursor0` and Qt platform plugins are present on
    Ubuntu 22.04 / Debian 12 / Linux Mint 21.
- **Test platform:** Ubuntu 22.04 LTS, Linux Mint 21, Debian 12.
- **Acceptance:** `sudo dpkg -i netneighbor_2.0.0_amd64.deb && netneighbor` launches
  the application with tray and discovery working.

### I-3 Windows packaging (PyInstaller + Inno Setup installer) 📋

- **Files:** `packaging/windows/build.ps1`, `packaging/windows/build_installer.ps1`,
  `packaging/windows/*.iss` (Inno Setup spec)
- **Change:**
  - PyInstaller spec: update entry point to `main.py`, include `locale/` tree,
    `assets/`, `config/` (JSON configs), PySide6 Qt plugins (platform, imageformats,
    iconengines).
  - Add `--windowed` flag (no console window on launch).
  - Include VC++ redistributable check in Inno Setup installer.
  - Verify autostart registry entry written by the app survives an installed-package
    path (absolute path to the installed `netneighbor.exe`).
  - Code-signing: document the step even if not automated (placeholder for future CI).
- **Test platform:** Windows 10 22H2, Windows 11 23H2.
- **Acceptance:** Installer runs cleanly, application launches from Start menu,
  system tray visible, autostart survives reboot.

### I-4 macOS packaging (PyInstaller `.app` + `.dmg`) 📋

- **Files:** `packaging/macos/build_app.sh`, `packaging/macos/*.spec`
- **Change:**
  - PyInstaller spec: entry point `main.py`, include locale/assets/config trees,
    PySide6 Qt plugins.
  - `.plist` — `LSUIElement = NO` (show in Dock), `NSHighResolutionCapable = YES`.
  - `dmg` creation via `create-dmg` or `hdiutil`.
  - Note: system tray on macOS requires `NSStatusBar` via Qt which should work
    out of the box with PySide6. Autostart uses the XDG `.desktop` path
    (`session_autostart.py` already handles macOS via `_xdg_apply_autostart`).
- **Test platform:** ⚠️ Cannot be validated without a macOS machine. Script will be
  written and reviewed but marked untested until a macOS build environment is available.
- **Acceptance (provisional):** Build script completes without errors on macOS 13+;
  application launches from `.app` bundle.

### I-5 CI / release pipeline 💡

- GitHub Actions workflow: build + package on push to `release/*` branch.
- Linux: Ubuntu runner → `.deb` + AppImage.
- Windows: `windows-latest` runner → PyInstaller `.exe` + Inno Setup `.exe`.
- macOS: `macos-latest` runner → `.app` + `.dmg` (untested by owner).
- Upload artifacts to GitHub Releases automatically.

---

## Implementation order (Theme E)

```
E-1 (cache + last_seen)  →  E-2 (UI indicator)  →  E-3 (NetBIOS TTL)
                                                  →  E-4 (WSD IP change)
                                                  →  E-5 (TCP probe)
```

E-1 and E-2 are independent of the discovery layer and carry no risk of regressions.
E-3 and E-4 touch discovery internals and should be validated on a live LAN before
merging. E-5 is the most complex; it requires careful thread-pool sizing and
timeout tuning to avoid slowing startup on large networks.

## Implementation order (Theme H)

```
H-1 (extract .pot)  →  H-2 (audit + translate, fr first)  →  H-3 (compile + test)
```

H-2 can run in parallel per language once the `.pot` is generated.
Recommend using `poedit` or `Lokalize` for the translation review step.

## Implementation order (Theme I)

```
I-1 (audit scripts)  →  I-2 (Linux, tested)
                     →  I-3 (Windows, tested)
                     →  I-4 (macOS, untested — scripted only)
                     →  I-5 (CI pipeline, after I-2 + I-3 validated)
```

I-1 is a prerequisite for all platform builds. I-2 and I-3 can proceed in parallel
once I-1 is done. I-4 is low priority until a macOS test machine is available.
I-5 should only be wired once I-2 and I-3 produce verified artifacts.

---

## Global roadmap summary

```
Themes A–D  ✅  Done
Theme E     📋  Next — device lifetime (E-1 → E-5)
Theme F     💡  Future — discovery enhancements
Theme G     💡  Future — UX polish
Theme H     ✅  Translation review (H-1 → H-3) — 74 strings, 8 languages
Theme I     📋  After H — packaging Linux + Windows; macOS scripted (I-1 → I-5)
```
