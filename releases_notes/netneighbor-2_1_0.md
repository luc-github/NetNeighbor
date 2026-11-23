# NetNeighbor

## Release 2.1.0

*2026-07-18 to 2026-11-23*

This release fixes critical performance issues on Windows, resolves device identity confusion when DHCP reuses IP addresses, improves mDNS discovery for multi-homed hosts and legacy responders, and adds support for disabling auto-generated commands with enhanced device categorization rules.

## 📋 Table of Contents

- [🚀 New Features](#new-features)
- [  - Device Command Customization](#device-command-customization)
- [  - Location Management with Policy Enforcement](#location-management-with-policy-enforcement)
- [🐛 Bug Fixes](#bug-fixes)
- [💪 Improvements](#improvements)
- [📝 Other Changes](#other-changes)
- [👥 Contributors](#contributors)
- [📊 Statistics](#statistics)

<a id="new-features"></a>

## 🚀 New Features

<a id="device-command-customization"></a>

### Device Command Customization

New ability to disable auto-generated commands (such as 'open http') for individual devices by using custom command overrides. Includes expanded automatic model detection rules for servers, phones, and smart TVs.

<sub>Commits: [`dba846c`](https://github.com/luc-github/netneighbor/commit/dba846c)</sub>

<a id="location-management-with-policy-enforcement"></a>

### Location Management with Policy Enforcement

Persistent location assignments for devices that survive application restarts. New location policy controls allow you to restrict discovered locations to a configured list, with optional automatic addition of new locations. User-assigned locations always take priority and are preserved across sessions.

<sub>Commits: [`a775502`](https://github.com/luc-github/netneighbor/commit/a775502)</sub>

<a id="bug-fixes"></a>

## 🐛 Bug Fixes

- **Windows Resource Exhaustion from ARP Lookups**: Fixed severe performance issue on Windows where concurrent neighbor table lookups spawned hundreds of hidden arp.exe and conhost.exe processes, exhausting desktop heap resources. Now uses native Windows APIs (GetIpNetTable2, IcmpSendEcho) with intelligent caching and single-flight request deduplication. ([`8c51c2c`](https://github.com/luc-github/netneighbor/commit/8c51c2c))
- **Device Icon and Identity Confusion on DHCP IP Reuse**: Fixed critical bug where devices taking over previously-used IP addresses inherited the old device's icon and identity. Device lookup now uses announced URLs instead of IP addresses, and NetBIOS/WS-Discovery rows are pinned to their original MAC address to detect when IPs change hands. ([`3c05400`](https://github.com/luc-github/netneighbor/commit/3c05400))
- **Incorrect Version Display on Linux**: Fixed About dialog showing version 0.0.0 on Linux systems. Version file is now correctly located across all installation methods (AppImage, .deb, tarball, source installs). ([`085b7f0`](https://github.com/luc-github/netneighbor/commit/085b7f0))
- **Lost Device Customizations on Restart**: Fixed bug where location, name, and device type overrides assigned through the context menu were lost on application restart due to missing persistence logic. ([`a775502`](https://github.com/luc-github/netneighbor/commit/a775502))
- **UI Rendering Glitches in Details View**: Fixed visual glitches that occurred in the device details panel. ([`c5cee6e`](https://github.com/luc-github/netneighbor/commit/c5cee6e))

<a id="improvements"></a>

## 💪 Improvements

- **Enhanced Multi-homed Host Discovery**: Improved discovery and aggregation of devices with multiple network interfaces (like a PC with Ethernet, VirtualBox, and APIPA). Hosts are now properly deduplicated by their unique identifier rather than individual IP addresses, preventing duplicate rows. ([`94cf969`](https://github.com/luc-github/netneighbor/commit/94cf969))
- **mDNS Discovery for Legacy Responders**: Added support for discovering embedded mDNS responders (such as Samba NAS devices) that announce services but don't respond to standard multicast queries. Now uses RFC 6762 §6.7 legacy-unicast fallback to query devices directly. ([`94cf969`](https://github.com/luc-github/netneighbor/commit/94cf969))
- **Local Machine Detection**: Improved automatic identification of the local machine running NetNeighbor by detecting when a device's IP belongs to a local network interface, reducing misclassification as 'Unknown Devices'. ([`94cf969`](https://github.com/luc-github/netneighbor/commit/94cf969))
- **Linux Neighbor Table Performance**: Eliminated redundant per-IP spawn calls on Linux by consolidating to use a shared cached neighbor table, improving discovery responsiveness. ([`8c51c2c`](https://github.com/luc-github/netneighbor/commit/8c51c2c))

<a id="other-changes"></a>

## 📝 Other Changes

- **Project Metadata Updates**: Updated project configuration files and metadata.

<a id="contributors"></a>

## 👥 Contributors

Thanks to all contributors who made this release possible!

- **[@luc-github](https://github.com/luc-github/luc-github)** (11 commits, +1722/-751)

<a id="statistics"></a>

## 📊 Statistics

- **Commits**: 11
- **👥 Contributors**: 1
- **Lines changed**: +1722 / -751

---
*Generated by Release Agent on 2026-11-23 22:18*