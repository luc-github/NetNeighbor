# NetBIOS in NetNeighbor

## Protocol overview

**NetBIOS over TCP/IP** (NBT) allows Windows hosts to advertise their computer name on
the LAN via UDP broadcast. NetNeighbor resolves names using Samba's **`nmblookup`** CLI
tool (Linux/macOS) or the Windows built-in **`nbtstat`** (Windows).

NetBIOS is a **supplementary source** — it enriches device names for hosts already
discovered via SSDP, mDNS, or WSD. It does not perform an IP scan; it resolves names for
IPs that other protocols have already found (or that the user has directed explicitly).

## NetBIOS name suffixes

Each NetBIOS registration includes a 1-byte suffix indicating the registration type:

| Suffix | Meaning |
|--------|---------|
| `<00>` | Workstation name (unique) — primary host identity |
| `<20>` | File server (unique) — SMB file sharing active |
| `<03>` | Messenger service (unique) |
| `<1e>` | Browser election group |
| `<1d>` | Local master browser (unique) |

NetNeighbor picks the display name preferring `<00>` (workstation), then `<20>` (file server),
then the first non-noise registration. Workgroup-only registrations (`WORKGROUP<00>`,
`__MSBROWSE__`) are filtered out.

## NetNeighbor implementation

| Layer | File | Class |
|-------|------|-------|
| Provider | `discovery/netbios.py` | `NetbiosDiscovery` — periodic `nmblookup` sweep |
| Orchestration | `discovery/manager.py` | merges name into existing device rows |

Discovery works in two modes:

1. **Broadcast sweep** — `nmblookup -S '*'` covers all hosts responding to NetBIOS broadcasts
   on the local subnet. Interval: 180 s (default).
2. **Directed probe** — `nmblookup -A <ip>` (or `nbtstat -A <ip>` on Windows) for specific IPs
   queued by `suggest_directed_ip()`. Used when WSD discovers a host with only a synthetic
   display name.

## Offline detection

`NetbiosDiscovery` tracks known hosts and their miss counts:

- `_nmb_known` — last emitted payload per IP
- `_nmb_miss_counts` — consecutive sweeps without a response

After **2 missed sweeps** (`_nmb_grace_sweeps`) the device is emitted as offline.
Transient failures (timeout, `OSError`) return early without penalising known devices.

## Windows support

On Windows, `nmblookup` is typically unavailable. NetNeighbor falls back to `nbtstat -A <ip>`
via `shutil.which("nbtstat")`. The `_parse_nbtstat_output()` function handles its output format.

All `subprocess` calls pass `CREATE_NO_WINDOW` + `STARTUPINFO` to prevent console flashes
in the frozen application. See `_subprocess_no_window_kwargs()`.

If neither `nmblookup` nor `nbtstat` is found, a warning is logged and NetBIOS discovery
is silently disabled — other protocols continue working normally.

## IP preference

When `nmblookup` returns the same name on multiple IPs, NetNeighbor picks the best candidate
via `_ip_rank()`:

| Priority | Address type |
|----------|-------------|
| 0 (best) | LAN IPv4 (private or link-local) |
| 1 | Public IPv4 |
| 2 | Global IPv6 |
| 3 | Private IPv6 |
| 4 | Link-local IPv6 (`fe80::`) |
| 5 | Loopback IPv4 |
| 6 | Loopback IPv6 |

## Configuration (`discovery.json`)

```json
{
  "netbios": {
    "enabled": true,
    "interval_seconds": 180,
    "timeout_seconds": 15
  }
}
```

Linux prerequisite: `samba-common-bin` (provides `nmblookup`). The `.deb` package lists
it as a runtime dependency.

## Debugging

- Enable `nmb` key in `~/.config/netneighbor/logging.json` for verbose probe logs
- If no NetBIOS names appear: check `which nmblookup` (Linux) or ensure `nbtstat` is reachable
- Broadcast sweep only covers hosts that respond to NBT broadcast on the local subnet;
  devices on different VLANs or behind a router will not respond
- Device detail tab shows the resolved NetBIOS name alongside SSDP/mDNS fields

## See also

- [`WSD.md`](WSD.md) — WS-Discovery (triggers directed NetBIOS probes for synthetic names)
- [`SSDP.md`](SSDP.md) — SSDP/UPnP pipeline
- [`MDNS.md`](MDNS.md) — mDNS/Bonjour pipeline
- [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md) — discovery manager overview
