# Changelog

## 0.8.0 — 2026-05

### Discovery & identity

- **Kernel neighbor cache**: resolve **MAC** from `/proc/net/arp` and `ip neigh` when metadata omits it (`utils/neighbor_mac.py`).
- **IPv4 surfacing**: when discovery only exposes **IPv6** (common for WSD + link-local) but ARP/neigh already has a **LAN IPv4** for the same MAC, show **IPv4 + IPv6** in list, overview, and protocol details (`format_device_ip_for_details` + `lookup_ipv4_for_mac`).
- **NetBIOS after WSD (IPv6-only rows)**: if the hostname is synthetic (`WSD-…`, `ws-…`) on **IPv6**, derive LAN IPv4 from neighbor/MAC and queue **`nmblookup -A <ipv4>`** so the NetBIOS name appears sooner (`discovery/manager.py`).
- **Fewer duplicate tiles**: bundle merge adds a **`mac:`** merge key using **neighbor-resolved MAC** so IPv4 vs IPv6 rows for the same host collapse earlier (`ui/device_list.py`).

### UI / tray / session

- **Provisional device affordance**: weak WSD-style names on **IPv6** and/or **remote icon fetch pending** — soft pulse (list + icons), italic/dim copy, richer tooltips (`ui/device_list.py`).
- **`Gtk.Dialog` decorations**: **`prepare_gtk_dialog`** disables header-bar dialogs that lost WM titlebars on some setups (`utils/gtk_dialog.py`, wired across main/details/list dialogs).
- **First launch**: after the session-startup onboarding dialog, hide the **main window to the tray** when available (recommended post-install UX).
- **CLI**: **`--start-minimized-to-tray`** hides to tray on startup (wired from `MainWindow` + skips initial `Gtk.Application` present/focus steal). **Login autostart** (`utils/session_autostart.py`) appends this flag only in **`~/.config/autostart/`** — the **applications menu `.desktop`** in packaging stays plain `Exec=netneighbor`.
- **Tray menu**: **`Minimize to tray`** row ( **`hide()`** ), sensitive only while the main window is **visible**.
- **`app.py`**: forwards unknown argv remnants to **`Gtk.Application.run`** after parsing CLI flags.

### Documentation

- This file; **`README.md`**, **`docs/MAINTENANCE.md`**, **`docs/UI_ARCHITECTURE.md`**, **`USER_DOCUMENTATION.md`**, and **`docs/ROADMAP.md`** updated for 0.8.0.

---

Older releases did not maintain a changelog in-tree; **`git log`** remains the historic source prior to **0.8.0**.
