# NetNeighbor — User Documentation (MVP)

This document is intentionally short and focused on the current usable scope:

- general presentation
- SSDP usage

Future topics (mDNS, packaging mode, tray mode, file manager integration, service mode) will be
added when decisions are finalized.

## General presentation

NetNeighbor helps you see devices that announce themselves on your local network.

Current behavior:

- one active NetNeighbor instance at a time
- device list and icon view
- categories in the sidebar
- optional desktop notifications for status changes
- right-click actions on devices (open, details, monitor, type override, icon source)

Important scope note:

- NetNeighbor listens to discovery announcements.
- It does not run a full active network scan.

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

- mDNS section is not included yet in this user doc
- protocol coverage is SSDP-first in the current milestone
- packaging/distribution mode is still under decision
