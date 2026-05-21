# WS-Discovery in NetNeighbor

## Protocol overview

**WS-Discovery** (WSD) is a SOAP/UDP multicast protocol used by Windows PCs, printers, and
other Windows-compatible devices to announce themselves on the LAN.

| Message | Direction | Meaning |
|---------|-----------|---------|
| Probe | Client → multicast `239.255.255.250:3702` | "Who is there?" |
| ProbeMatch | Device → unicast | Device responds with its endpoint reference (EPR) |
| Hello | Device → multicast | Unsolicited announcement on join |
| Bye | Device → multicast | Device leaving |

Each device exposes an **Endpoint Reference (EPR)** — a `urn:uuid:…` URI — plus optional
**XAddrs** (transport addresses like `http://hostname:5357/…`) and **Types** (DPWS QNames
describing the device class).

NetNeighbor uses the [`WSDiscovery`](https://pypi.org/project/WSDiscovery/) PyPI package
(`wsdiscovery`) which handles the raw SOAP/UDP layer. WSD is also supplemented by `wsdd`
integration — see `discovery/wsdd_client.py` for the local `wsdd` daemon socket path.

## NetNeighbor implementation

| Layer | File | Class |
|-------|------|-------|
| Provider | `discovery/wsd.py` | `WSDDiscovery` — `ThreadedWSDiscovery` wrapper, sweep/TTL logic |
| Supplementary | `discovery/wsdd_client.py` | `WsddSocketDiscovery` — polls local `wsdd` Unix socket |
| Orchestration | `discovery/manager.py` | `add_or_update_device` — WSD/wsdd merge, notify listeners |

`wsdiscovery` and `zeroconf` are imported lazily inside `start()` via `_ensure_wsd_libs()` to
avoid blocking the Qt event loop at startup (saves ~500 ms on cold start).

## Device name resolution

WSD devices often lack a human-readable label. NetNeighbor tries these sources in order:

1. **Scopes** — tail of scope URIs after the last `/` (skips UUID-only tails and synthetic `WSD-…` labels)
2. **XAddrs hostnames** — non-IP hostnames embedded in transport URLs (often the Windows computer name)
3. **Reverse DNS** — PTR lookup via `gethostbyaddr` (fast; often empty on home LANs)
4. **EPR tail** — last 8 hex digits of the UUID, formatted as `WSD ·xxxxxxxx` (fallback)

Synthetic display names (`WSD ·…`, `WSD host`) are flagged by `is_synthetic_wsd_display_name()`.
`DiscoveryManager` uses this flag to request a directed NetBIOS probe
(`netbios.suggest_directed_ip()`) so the Windows PC name can be resolved via `nmblookup -A`.

## IP / endpoint selection

When a device advertises multiple XAddrs or a mix of IPv4 and link-local IPv6 (`fe80::`),
NetNeighbor prefers LAN IPv4 (`_endpoint_address_rank`). A **sticky IPv4 cache** with a 1-hour
TTL remembers the last known LAN IPv4 per EPR when a later probe only lists link-local IPv6
(common on Wi‑Fi mini PCs after roaming).

## Type classification

WSD QNames (namespaces + local names) are inspected to classify the device:

| Heuristic | Type assigned |
|-----------|---------------|
| DPWS `devprof:Device` or `Computer` / `ComputerDevice` local name | `computer` |
| `PrintDevice`, `Printer`, WDP print namespace | `networkprinter` |
| Both PC and printer QNames present | `computer` (PC wins) |
| No matching QNames | `computer` (default) |

The full logic is in `_infer_type_from_qnames()` and `_qname_suggests_computer()` /
`_qname_suggests_printer()`.

## Offline detection

`WSDDiscovery` tracks known EPRs and their miss counts:

- `_wsd_known_eprs` — last payload per EPR
- `_wsd_miss_counts` — consecutive sweeps without a response

After **2 missed sweeps** (`_wsd_grace_sweeps`) the device is emitted as offline.
Transient probe failures return early without penalising known devices.

`WsddSocketDiscovery` applies the same TTL logic via `_wsdd_known` / `_wsdd_miss_counts`.

## Configuration (`discovery.json`)

```json
{
  "wsd": {
    "enabled": true,
    "interval_seconds": 90,
    "timeout_seconds": 8
  }
}
```

There is no `wsd_rules.json` — type classification is driven entirely by QName inspection.

## Debugging

- Enable `wsd` key in `~/.config/netneighbor/logging.json` for verbose probe/response logs
- Details dialog → first tab: WSD-specific fields include EPR UUID, XAddrs, Types
- If a Windows PC appears as `WSD ·xxxxxxxx`, the scope/hostname resolution failed;
  NetBIOS (`nmblookup -A <ip>`) is queued automatically to resolve the computer name
- Link-local IPv6 addresses (`fe80::`) in the overview are expected for Windows PCs that
  advertise only IPv4 in HTTP XAddrs — the IPv6 line may be empty

## See also

- [`SSDP.md`](SSDP.md) — SSDP/UPnP pipeline
- [`MDNS.md`](MDNS.md) — mDNS/Bonjour pipeline
- [`NETBIOS.md`](NETBIOS.md) — NetBIOS supplementary name resolution
- [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md) — discovery manager overview
