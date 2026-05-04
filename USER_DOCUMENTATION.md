# NetNeighbor — User Documentation (MVP)

This document is intentionally short and focused on the current usable scope:

- general presentation
- SSDP usage

**Tray & session:** from **View → Preferences** you can keep the app in the **system tray** when
closing the window, start **minimized to the tray**, and optionally add a **login autostart** entry.
Login autostart uses `--start-minimized-to-tray` on the command line; the Applications menu launcher
does **not** pass this flag — only the session autostart file does.
The tray icon menu adds **Minimize to tray** when the window is visible (same hide-to-tray behavior as closing with **close-to-tray** enabled).
On the **first run**, a short dialog proposes **starting NetNeighbor at login** (checkbox **on** by
default; you can turn it off or click **Not now** — change anytime in Preferences).
**F11** toggles **fullscreen** (handy in icon view). See root [`README.md`](README.md) and
[`docs/MAINTENANCE.md`](docs/MAINTENANCE.md) for dependencies (AppIndicator, `nmblookup`).

**Open:** for device type **computer** (typical Windows WSD row), **Open** in the menu is disabled
for now; use **Details** instead — the old default URL was a non-browser metadata endpoint.

## General presentation

NetNeighbor helps you see devices that announce themselves on your local network.

Current behavior:

- one active NetNeighbor instance at a time
- device list and icon view
- categories in the sidebar
- optional desktop notifications for status changes
- right-click actions on devices (open when relevant, details, monitor, type override, rename, location, icon appearance)

Important scope note:

- NetNeighbor listens to discovery announcements.
- It does not run a full active network scan.

### Autodetection is heuristic

Classification and friendly names are inferred from **SSDP**, **mDNS**, and **cached** SSDP/XML data.
That logic is tuned from real LAN behaviour; it will not always match every device or firmware. Treat
auto-detected **type** and **name** as best-effort hints.

When something is wrong, you have two complementary mechanisms:

1. **Overrides (per device)** — via the context menu / details flow: **type**, **display name**,
   **location**, and **icon** can be set explicitly. These preferences override discovery for that device
   and are meant for quick, precise fixes on your network.

2. **User rules** — optional JSON overlays in `~/.config/netneighbor/` extend or adjust **mDNS** and
   **SSDP** matching (see developer docs [`COMMUNITY_OVERRIDES.md`](docs/COMMUNITY_OVERRIDES.md)). Rules
   that work well across setups can be proposed as **bundled app rules** so they become generic defaults.

## SSDP section

### What SSDP means in NetNeighbor

SSDP is the discovery protocol used by many UPnP/DLNA-capable devices (routers, media devices,
some printers, smart devices, etc.).

NetNeighbor listens for SSDP traffic and periodically sends discovery queries to refresh state.

### What you can expect in the UI

- devices can appear progressively after startup
- a device can change from offline to online when new SSDP announcements are received
- monitored offline devices are displayed greyed out
- right-click -> `SSDP details` shows parsed fields and raw XML when available

### Online/offline behavior (SSDP)

NetNeighbor handles SSDP status with two paths:

- `byebye` received from the device: immediate offline
- timeout expiration (no SSDP seen for the device): offline after timeout

Timeout policy:

- when SSDP `CACHE-CONTROL: max-age=...` exists, timeout uses approximately `2 x max-age`
- otherwise, a default fallback timeout is used

This reduces rapid flapping for devices that sleep and wake with delayed announcements.

### Why counts can change over time

At startup, device count may increase as more SSDP responses arrive.
This is normal for passive/refresh-based discovery.

### Troubleshooting (SSDP)

If expected devices do not appear:

- confirm devices are on the same local network
- confirm local firewall allows SSDP multicast traffic (UDP 1900)
- wait a short period after startup for discovery responses
- use `Reload` from the `View` menu

If a device appears/disappears:

- check whether the device goes to sleep
- remember `byebye` causes immediate offline
- timeout-based offline can occur if announcements stop

## Current limits

- SSDP is documented in depth below; mDNS behaviour exists in the app — see developer docs
  [`docs/MDNS_INTEGRATION.md`](docs/MDNS_INTEGRATION.md) for mechanics.
- packaging/distribution mode is still under decision
