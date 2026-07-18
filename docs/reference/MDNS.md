# mDNS in NetNeighbor

## Protocol overview

**mDNS** (multicast DNS / DNS-SD) advertises services on the local network under `*.local.` names.
NetNeighbor uses `zeroconf` browsing callbacks; it does not perform an active LAN scan.

Typical service types discovered:

| Type | Meaning |
|------|---------|
| `_http._tcp` | Web UI |
| `_smb._tcp`, `_microsoft-ds._tcp` | File sharing |
| `_ssh._tcp` | SSH |
| `_ftp._tcp` | FTP |
| `_telnet._tcp` | Telnet |
| `_esp3d._tcp` | ESP3D firmware |

## Code map

| Area | File / symbol | Role |
|------|---------------|------|
| Provider | `discovery/mdns.py` | `MDNSDiscovery` — zeroconf browser/listener, TXT decode, host aggregation |
| Contract | `discovery/base.py` | `BaseDiscovery._emit("device", payload)` → normalized dict to manager |
| Orchestration | `discovery/manager.py` | Receives `mdns` payloads, applies overrides, stores and notifies |
| Mapping hints | `config/device_types.json` | Per-service defaults (`type`, `icon`, `default_port`) |
| UI payload | `utils/details_payload.py` | `build_mdns_payload` — per-service sections for the Services tab |
| Rules | `config/mdns_rules.json` | TXT → summary line mappings and optional `type_rules` |

## Browse strategy

- Service types from `config/device_types.json` (`mdns` section) are browsed immediately.
- **DNS-SD enumeration** (`_services._dns-sd._udp`) runs in background to catch unlisted types.
- Enumeration repeats on a timer and on `refresh()` — no types need to be listed statically.

## Host-level aggregation

mDNS announces per-service. NetNeighbor aggregates services to one logical host entry:

1. Track per-service payloads `(service_type, instance_name)`
2. Map each service to a host key — the **SRV target** (`ServiceInfo.server`, e.g.
   `DESKTOP-LUCTW.local.`), unique per machine; fallback to IP, then instance hostname
3. Emit an aggregated payload with all services under `metadata["services"]`

A device like ESP3D exposing both `_esp3d._tcp` and `_telnet._tcp` appears as one entry
with both services visible in the Details dialog.

## Multi-homed hosts and the local machine

An important real-world case (hit on the PC hosting NetNeighbor itself — see
`utils/local_host.py`): a Windows host with several network interfaces advertises **all**
its addresses in one mDNS record — real LAN NIC (DHCP), VirtualBox/Hyper-V host-only
switches (`192.168.56.1`, `192.168.53.1`, …), and APIPA `169.254.x.x` fallbacks on
disconnected adapters. Two consequences the pipeline must absorb:

- **Unstable address ordering.** zeroconf returns A/AAAA records in no guaranteed order,
  so trusting `addresses[0]` makes the same host flap between its interfaces across
  resolutions. Each flap used to materialise as a *distinct* device row (device keys are
  endpoint-based), so one PC showed up 3–4 times — and since hide/monitor prefs match by
  `host:name:` when no MAC/UID is available, hiding one row hid them all.
  Fix: `choose_best_address()` ranks candidates (routable LAN IPv4 → virtual-switch IPv4 →
  routable IPv6 → link-local) with a deterministic tie-break, both when building a
  per-service payload and across rows when aggregating a host.
- **Partial record sets.** Ranking alone is not enough: a zeroconf resolution can
  momentarily surface *only* the APIPA A record, so the "best" of that set is still
  link-local. Each such flap retired the LAN endpoint (offline) — and since offline
  non-monitored rows are dropped, the host vanished from the UI between flips. Fix:
  `_sticky_host_ip()` remembers the last rank-0 (routable) address per host for
  `_STICKY_HOST_IP_TTL_S` (10 min) and refuses to downgrade the aggregate endpoint while
  that memory is fresh; a *new* routable address (DHCP renewal) still replaces it
  immediately, and a host that genuinely lost its LAN falls back after the TTL.
- **Host key must not be the IP.** Keying hosts by IP split a multi-homed machine into one
  aggregate per interface and the stale-endpoint retirement in `_on_service_change` never
  fired. Keying by the SRV target keeps one aggregate; when the chosen endpoint changes
  (e.g. DHCP renewal), the previous endpoint is emitted offline and replaced.

The **local machine** is a further special case: Windows never answers WSD probes sent
from the host itself (verified with `fdrespub` running and a Private network profile —
remote hosts answer, the local stack does not), and NetBIOS browsing does not return the
local host either. Its only discovery rows are incidental mDNS adverts from user apps
(e.g. Spotify's `_spotify-connect._tcp`, which carries no host identity and would leave
the PC in "Unknown Devices" with a speaker icon). The manager therefore classifies any
still-unknown device whose IP belongs to a local interface as `computer`
(`_apply_local_host_identity` in `discovery/manager.py`, flag `metadata["local_host"]`);
explicit user type overrides still win.

## Legacy-unicast resolution fallback

Another real-world case (hit on "LUC-NAS", a Samba host with an embedded mDNS responder):
some responders announce PTRs — or their PTRs keep echoing through other clients'
known-answer sections — but **never answer standard multicast queries**, neither QM nor QU.
`zc.get_service_info()` then always returns `None` and the host silently never appears,
even though "discovery messages" for it are visible in the logs. Such responders do answer
**one-shot legacy queries** (RFC 6762 §6.7): a query sent from an ephemeral port gets a
unicast reply, recognisable by its TTL ≤ 10.

`_legacy_unicast_service_info()` handles this: when `get_service_info()` fails, the
instance label (must look like a hostname — no spaces/punctuation) is resolved as
`<label>.local` through the **OS resolver** (Windows resolves `.local` natively via
mDNS/LLMNR; Linux via nss-mdns), then the host is queried directly on `<ip>:5353` for
SRV/TXT. Guard rails: results (including failures) are cached for 60 s per service,
public IPs from DNS-suffix/ISP-redirect artifacts are rejected (`is_private` required),
and the SRV port falls back to the type map's `default_port`.

## URL policy

A presentation URL is built **only if `_http._tcp` is actually present**. Port numbers alone do not imply HTTP — this avoids wrong browser links for non-HTTP services on common ports.

## TXT records

- Raw `ServiceInfo.text` is decoded preserving duplicate keys and ordering → `metadata["services"][].txt_records`
- A flat `metadata["txt"]` (last-key-wins) is kept for type heuristics
- Details dialog shows one expandable row per service with TXT pairs nested under it

## Type heuristics

Type starts from `device_types.json` (per-service mapping), then refined entirely by the
`type_rules` in `mdns_rules.json` (first match wins). All classification heuristics
(`fluidnc→cnc`, `laserjet→networkprinter`, `synology/qnap/nas→nas`, 3D-printer/CNC keywords, …)
live in that JSON — there are no hardcoded type rules in `mdns.py`.

## Online / offline lifecycle

A host stays online while at least one tracked service remains. Removing one service updates the aggregated payload; offline is emitted only when all services are gone — avoids flapping for multi-service hosts.

When the last service disappears while the host is still otherwise alive, the removal is delayed by a **120 s grace period** to absorb mDNS TTL jitter. A user-initiated `refresh()` reschedules any pending grace-removes to a short **15 s window**, so a device that truly left the network disappears quickly instead of lingering for the full grace period.

Brutal disconnections (power loss, cable pull) emit no byebye and would otherwise persist until the mDNS TTL expires. The manager's periodic ICMP probe (see [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md#reachability-probe--offline-removal)) flips such hosts offline within ~180 s and drops non-monitored ones from the list.

---

## Rules file (`config/mdns_rules.json`)

Lets you **map TXT keys onto summary lines** and **extend type classification** without Python changes.
User overlay: `~/.config/netneighbor/mdns_rules.json` — merged with the bundled file.
See [`COMMUNITY_OVERRIDES.md`](COMMUNITY_OVERRIDES.md).

> **Single source of truth.** This bundled file *is* the rules — there is no Python copy. The
> startup integrity check (`utils/config_integrity.py`) refuses to launch with a "corrupted
> installation, please reinstall" dialog if it is missing or not valid JSON.

### `summary_from_txt`

Ordered list; each entry emits at most one first-tab detail row.

| Field | Type | Meaning |
|-------|------|---------|
| `label` | string | Detail column heading (match SSDP wording to enable cross-protocol merge) |
| `keys` | list | TXT key aliases, priority order, case-insensitive |

### `type_rules`

Evaluated after fixed heuristics. First match wins.

| Field | Type | Meaning |
|-------|------|---------|
| `contains_any` | list | Lowercase substrings searched in service key, name, and TXT pairs |
| `type` | string | Internal type id (e.g. `nas`, `router`) |

### How to extend

1. Add `summary_from_txt` entries matching your devices' actual TXT keys (check Services tab in Details).
2. Use the same `label` as SSDP counterparts so SSDP+mDNS detail rows merge correctly.
3. Add `type_rules` with specific `contains_any` before generic ones.
4. Validate: `python -m json.tool config/mdns_rules.json`
5. Restart the app (rules are cached at startup).

## Debugging

- Enable `mdns` in `~/.config/netneighbor/logging.json`
- Details dialog → Services tab: verify TXT records, services list, URL present only when `_http._tcp` exists

## See also

- [`SSDP.md`](SSDP.md) — parallel SSDP pipeline
- [`WSD.md`](WSD.md) — WS-Discovery pipeline
- [`NETBIOS.md`](NETBIOS.md) — NetBIOS pipeline
- [`COMMUNITY_OVERRIDES.md`](../contributing/COMMUNITY_OVERRIDES.md) — user overlay rules
- [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md) — discovery manager overview
