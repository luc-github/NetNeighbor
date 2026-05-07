# File details_payload.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Build protocol detail payloads from normalized metadata."""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse


from model.device import Device

_FE80_QUICK_RE = re.compile(r"(?<![0-9a-fA-F:])(fe80:[0-9a-fA-F:]+(?:%[0-9]+)?)", re.IGNORECASE)
from utils.mdns_rules import cached_mdns_rules, summary_field_labels_norm, summary_rows_from_rules
from utils.neighbor_mac import lookup_ipv4_for_mac, lookup_mac_from_neighbor_cache


def _parse_listen_port(value) -> int | None:
    try:
        p = int(value)
    except (TypeError, ValueError):
        return None
    if p < 0 or p > 65535:
        return None
    return p


def collect_ports_from_device(device: Device) -> list[int]:
    """Gather TCP/UDP-style listening ports from a device row and its metadata."""
    ports: list[int] = []
    md = device.metadata if isinstance(device.metadata, dict) else {}
    root = _parse_listen_port(getattr(device, "port", None))
    if root is not None:
        ports.append(root)
    if device.source == "ssdp":
        xml_fields = md.get("xml_fields") if isinstance(md.get("xml_fields"), dict) else {}
        purl = xml_fields.get("presentationURL")
        if isinstance(purl, str) and purl.strip():
            parsed = urlparse(purl.strip())
            if parsed.port is not None:
                po = _parse_listen_port(parsed.port)
                if po is not None:
                    ports.append(po)
        recs = xml_fields.get("services_records")
        if isinstance(recs, list):
            for entry in recs:
                if not isinstance(entry, dict):
                    continue
                po = _parse_listen_port(entry.get("port"))
                if po is not None:
                    ports.append(po)
    elif device.source == "mdns":
        services = md.get("services")
        if isinstance(services, list):
            for svc in services:
                if not isinstance(svc, dict):
                    continue
                po = _parse_listen_port(svc.get("port"))
                if po is not None:
                    ports.append(po)
    return ports


def aggregate_ports_display(*devices: Device | None) -> str:
    """Comma-separated unique ports for one or more devices (e.g. merged SSDP + mDNS host)."""
    found: list[int] = []
    for d in devices:
        if d is None:
            continue
        found.extend(collect_ports_from_device(d))
    uniq = sorted(set(found))
    if not uniq:
        return _value_or_unavailable(None)
    return ", ".join(str(p) for p in uniq)


def with_aggregate_ports_field(fields: list[tuple[str, str]], *devices: Device | None) -> list[tuple[str, str]]:
    """Remove Port/Ports rows and insert a single aggregated Ports row after IP (SSDP + mDNS combined view)."""
    skip = frozenset({"port", "ports"})

    def _nk(key: str) -> str:
        return str(key).strip().lower()

    cleaned = [(k, v) for k, v in fields if _nk(k) not in skip]
    insert_idx = 0
    for i, (k, _v) in enumerate(cleaned):
        if _nk(k) == "ip":
            insert_idx = i + 1
            break
    # Order IP → Type → Ports when Type row exists immediately after IP.
    if insert_idx < len(cleaned) and _nk(cleaned[insert_idx][0]) == "type":
        insert_idx += 1
    return cleaned[:insert_idx] + [(_("Ports"), aggregate_ports_display(*devices))] + cleaned[insert_idx:]


def format_device_type_for_details(device: Device) -> str:
    """Human-readable type line for the device details tab (slug → translated label)."""
    raw = (getattr(device, "type", None) or "unknown")
    t = str(raw).strip().lower()
    known = {
        "unknown": _("Unknown"),
        "http": _("HTTP device"),
        "https": _("HTTPS device"),
        "router": _("Router"),
        "mediaserver": _("Media server"),
        "printer": _("Printer"),
        "networkprinter": _("Printer"),
        "multifunction_printer": _("Multifunction printer"),
        "smartspeaker": _("Smart speaker"),
        "smarttv": _("Smart TV"),
        "smartdevice": _("Smart device"),
        "camera": _("Camera"),
        "homeappliance": _("Home appliance"),
        "cnc": _("CNC"),
        "3dprinter": _("3D printer"),
        "nas": _("NAS"),
        "computer": _("Computer"),
        "esp32": _("ESP3D / firmware"),
        "scanner": _("Scanner"),
    }
    if t in known:
        return known[t]
    if t:
        return t.replace("_", " ")
    return _("Unknown")


def _value_or_unavailable(value) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, str) and not value.strip():
        return "unavailable"
    return str(value)


def collect_mac_from_device_metadata(metadata: dict) -> str | None:
    """MAC from flat keys, SSDP ``xml_fields``, or mDNS TXT (root and per-service)."""
    if not isinstance(metadata, dict):
        return None
    for k in ("mac", "mac_address", "MAC", "macAddress"):
        v = metadata.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    xf = metadata.get("xml_fields")
    if isinstance(xf, dict):
        v = xf.get("mac")
        if isinstance(v, str) and v.strip():
            return v.strip()

    def _from_txt(txt: dict) -> str | None:
        if not isinstance(txt, dict):
            return None
        for raw_key, raw_val in txt.items():
            nk = str(raw_key).strip().lower().replace("-", "_")
            if nk in {"mac", "macaddress", "mac_address", "ether", "hardware_address"} or nk.endswith(
                "macaddress"
            ):
                s = raw_val.strip() if isinstance(raw_val, str) else str(raw_val).strip()
                if s:
                    return s
        return None

    hit = _from_txt(metadata.get("txt") if isinstance(metadata.get("txt"), dict) else {})
    if hit:
        return hit
    services = metadata.get("services")
    if isinstance(services, list):
        for svc in services:
            if isinstance(svc, dict) and isinstance(svc.get("txt"), dict):
                hit = _from_txt(svc["txt"])
                if hit:
                    return hit
    return None


def resolve_mac_for_device(device: Device) -> str | None:
    """MAC from discovery metadata, else kernel ARP / IPv6 neighbor cache if available."""
    md = device.metadata if isinstance(device.metadata, dict) else {}
    hit = collect_mac_from_device_metadata(md)
    if hit:
        return hit
    return lookup_mac_from_neighbor_cache(getattr(device, "ip", None))


def _format_ip_one_line(ip_str: str) -> str:
    """Match list/overview IPv6 compaction (link-local tag)."""
    s = (ip_str or "").strip()
    if not s:
        return ""
    try:
        a = ipaddress.ip_address(s.split("%", 1)[0])
        c = a.compressed
        if isinstance(a, ipaddress.IPv6Address) and a.is_link_local:
            return f"{c} (LL)"
        return c
    except ValueError:
        return s


def format_device_ip_for_details(device: Device) -> str:
    """IP from discovery, plus IPv4 from neighbor cache when only IPv6 is on the device row but ARP knows the MAC."""
    base = (getattr(device, "ip", None) or "").strip()
    if not base:
        return base
    try:
        parsed = ipaddress.ip_address(base.split("%", 1)[0])
    except ValueError:
        return base
    if isinstance(parsed, ipaddress.IPv4Address):
        return base
    mac = resolve_mac_for_device(device)
    v4 = lookup_ipv4_for_mac(mac)
    base_fmt = _format_ip_one_line(base)
    if not v4:
        return base_fmt
    return f"{v4}  ·  {base_fmt}"


def build_ssdp_payload(device: Device) -> tuple[
    list[tuple[str, str]],
    str | None,
    list[tuple[str, str, str]],
    list[tuple[str, str]],
    str | None,
]:
    metadata = device.metadata
    xml_fields = metadata.get("xml_fields")
    xml_data = metadata.get("xml")
    xml_fields = xml_fields if isinstance(xml_fields, dict) else {}
    services_raw = xml_fields.get("services_description")
    services_list: list[tuple[str, str, str]] = []
    services_records_raw = xml_fields.get("services_records")
    if isinstance(services_records_raw, list):
        for entry in services_records_raw:
            if not isinstance(entry, dict):
                continue
            service = _value_or_unavailable(entry.get("service"))
            target = _value_or_unavailable(entry.get("target"))
            port = _value_or_unavailable(entry.get("port"))
            services_list.append((service, target, port))
    if isinstance(services_raw, str) and services_raw.strip():
        # ESP3D/SSDP strings are often a comma-separated list of service URNs.
        # Normalize separators and split into individual entries.
        normalized = services_raw.replace("\n", ",")
        parts = [p.strip() for p in normalized.split(",") if p.strip()]
        target_default = device.ip
        port_default = str(device.port)
        presentation_url = xml_fields.get("presentationURL")
        if isinstance(presentation_url, str) and presentation_url.strip():
            parsed = urlparse(presentation_url.strip())
            if parsed.hostname:
                target_default = parsed.hostname
            if parsed.port is not None:
                port_default = str(parsed.port)
        if not services_list:
            services_list = [(part, target_default, port_default) for part in parts]

    if isinstance(device.last_seen, datetime):
        try:
            last_seen_text = device.last_seen.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            last_seen_text = str(device.last_seen)
    else:
        last_seen_text = _value_or_unavailable(device.last_seen)
    fields = [
        (_("IP"), format_device_ip_for_details(device)),
        (_("Type"), format_device_type_for_details(device)),
        (_("Ports"), aggregate_ports_display(device)),
        (_("Location"), _value_or_unavailable(metadata.get("user_location"))),
        (_("Last seen"), last_seen_text),
        (_("Friendly name"), _value_or_unavailable(xml_fields.get("friendlyName"))),
        (_("Information"), _value_or_unavailable(metadata.get("information"))),
        (_("Manufacturer"), _value_or_unavailable(xml_fields.get("manufacturer"))),
        (_("Manufacturer URL"), _value_or_unavailable(xml_fields.get("manufacturerURL"))),
        (_("Model"), _value_or_unavailable(xml_fields.get("modelName"))),
        (_("Model URL"), _value_or_unavailable(xml_fields.get("modelURL"))),
        (_("MAC address"), _value_or_unavailable(resolve_mac_for_device(device))),
    ]
    xml_location = metadata.get("location")
    xml_location_norm = xml_location if isinstance(xml_location, str) else None
    raw_xml = xml_data if isinstance(xml_data, str) and xml_data.strip() else None
    troubleshooting_fields: list[tuple[str, str]] = [
        (_("Serial number"), _value_or_unavailable(xml_fields.get("serialNumber"))),
        (
            _("Unique identifier"),
            _value_or_unavailable(
                xml_fields.get("UDN")
                or xml_fields.get("uniqueIdentifier")
                or metadata.get("udn")
                or metadata.get("usn")
            ),
        ),
    ]
    return fields, raw_xml, services_list, troubleshooting_fields, xml_location_norm


def _svc_type_short(raw: Any) -> str:
    text = "" if raw is None else str(raw).strip()
    return text.replace(".local.", "").strip(".")


def _detail_field_usable(text: object) -> bool:
    stripped = "" if text is None else str(text).strip().lower()
    return bool(stripped and stripped != "unavailable")


def _txt_pairs_from_service_dict(svc: dict[str, Any]) -> list[tuple[str, str]]:
    pairs_raw = svc.get("txt_records")
    pairs: list[tuple[str, str]] = []
    if isinstance(pairs_raw, list):
        for row in pairs_raw:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                pairs.append((str(row[0]), "" if row[1] is None else str(row[1])))
    elif isinstance(svc.get("txt"), dict):
        svc_txt = svc["txt"]
        pairs = [
            (str(key), str(value))
            for key, value in sorted(svc_txt.items(), key=lambda item: str(item[0]).lower())
        ]
    return pairs


def build_mdns_payload(device: Device) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    """Return summary fields plus one structured section per mDNS service (TXT nested in UI).

    Sections are consumed by DeviceDetailsDialog as expand/collapse rows (revealer + header).
    """
    metadata = device.metadata if isinstance(device.metadata, dict) else {}
    hostname_raw = metadata.get("hostname")
    server_raw = metadata.get("server")
    display_host = hostname_raw if _detail_field_usable(hostname_raw) else server_raw

    if isinstance(device.last_seen, datetime):
        try:
            last_seen_text = device.last_seen.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            last_seen_text = str(device.last_seen)
    else:
        last_seen_text = _value_or_unavailable(device.last_seen)
    # xml_fields may be hydrated from the SSDP profile cache even on mDNS-only devices.
    xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
    fields = [
        (_("IP"), format_device_ip_for_details(device)),
        (_("Type"), format_device_type_for_details(device)),
        (_("Ports"), aggregate_ports_display(device)),
        (_("Location"), _value_or_unavailable(metadata.get("user_location"))),
        (_("Last seen"), last_seen_text),
        (_("Friendly name"), _value_or_unavailable(xml_fields.get("friendlyName"))),
        (_("Hostname"), _value_or_unavailable(display_host)),
        (_("MAC address"), _value_or_unavailable(resolve_mac_for_device(device))),
        (_("Manufacturer"), _value_or_unavailable(xml_fields.get("manufacturer"))),
        (_("Manufacturer URL"), _value_or_unavailable(xml_fields.get("manufacturerURL"))),
        (_("Model"), _value_or_unavailable(xml_fields.get("modelName"))),
        (_("Model URL"), _value_or_unavailable(xml_fields.get("modelURL"))),
        (_("Information"), _value_or_unavailable(metadata.get("information"))),
    ]

    for label, raw in summary_rows_from_rules(metadata):
        fields.append((label, _value_or_unavailable(raw)))

    sections: list[dict[str, Any]] = []

    services_meta = metadata.get("services")
    if isinstance(services_meta, list) and services_meta:
        for svc in services_meta:
            if not isinstance(svc, dict):
                continue
            svc_type = svc.get("service") or metadata.get("service")
            port_raw = svc.get("port")
            if port_raw is None:
                port_raw = device.port
            try:
                port_int = int(port_raw or 0)
            except (TypeError, ValueError):
                port_int = 0
            target = svc.get("server") or svc.get("hostname") or server_raw or hostname_raw
            target_str = _value_or_unavailable(target)
            svc_type_str = _value_or_unavailable(svc_type)

            txt_pairs = _txt_pairs_from_service_dict(svc)
            instance_raw = svc.get("instance")
            instance = instance_raw.strip() if isinstance(instance_raw, str) else ""
            short = _svc_type_short(svc_type_str)
            if instance:
                heading = f"{instance} — {short} — {port_int}"
            else:
                heading = f"{short} — {port_int}"

            sections.append(
                {
                    "heading": heading,
                    "service_type": svc_type_str,
                    "target": target_str,
                    "port": str(port_int),
                    "txt_records": txt_pairs,
                }
            )

    if not sections:
        svc_fallback = metadata.get("service") or "mDNS"
        txt_fallback: list[tuple[str, str]] = []
        txt_data = metadata.get("txt")
        if isinstance(txt_data, dict):
            txt_fallback = [
                (str(key), str(value))
                for key, value in sorted(txt_data.items(), key=lambda item: str(item[0]).lower())
            ]
        elif isinstance(txt_data, list):
            txt_fallback = [(f"item_{i + 1}", str(v)) for i, v in enumerate(txt_data)]
        elif isinstance(txt_data, str) and txt_data.strip():
            txt_fallback = [("txt", txt_data)]
        svc_str = _value_or_unavailable(svc_fallback)
        short_fb = _svc_type_short(svc_str)
        try:
            p = int(device.port or 0)
        except (TypeError, ValueError):
            p = 0
        sections.append(
            {
                "heading": f"{short_fb} — {p}",
                "service_type": svc_str,
                "target": device.ip,
                "port": str(device.port),
                "txt_records": txt_fallback,
            }
        )

    return fields, sections


def first_link_local_ipv6_from_xaddrs(xaddrs: list[object] | None) -> str | None:
    """Pick first IPv6 link-local address embedded in WSD-style XAddrs / URLs."""
    if not xaddrs:
        return None
    for raw in xaddrs:
        if not isinstance(raw, str) or not raw.strip():
            continue
        line = raw.strip()
        try:
            parsed = urlparse(line)
        except ValueError:
            parsed = None
        if parsed and parsed.hostname:
            host = str(parsed.hostname).strip("[]")
            try:
                a = ipaddress.ip_address(host.split("%", 1)[0])
            except ValueError:
                a = None
            if isinstance(a, ipaddress.IPv6Address) and a.is_link_local:
                return a.compressed
        hit = _first_fe80_literal(line)
        if hit:
            return hit
    return None


def _first_fe80_literal(text: str) -> str | None:
    for m in _FE80_QUICK_RE.finditer(text or ""):
        cand = m.group(1).split("%", 1)[0]
        try:
            a = ipaddress.ip_address(cand)
        except ValueError:
            continue
        if isinstance(a, ipaddress.IPv6Address) and a.is_link_local:
            return a.compressed
    return None


def collect_link_local_ipv6(*devices: Device | None) -> str | None:
    """Best-effort link-local IPv6 from any protocol row (WSD xaddrs, wsdd address blob, device IP)."""
    for d in devices:
        if d is None:
            continue
        sip = str(getattr(d, "ip", "") or "").strip()
        if sip:
            try:
                a = ipaddress.ip_address(sip.split("%", 1)[0])
            except ValueError:
                a = None
            if isinstance(a, ipaddress.IPv6Address) and a.is_link_local:
                return a.compressed
        md = d.metadata if isinstance(d.metadata, dict) else {}
        xs = md.get("wsd_xaddrs")
        if isinstance(xs, list):
            hit = first_link_local_ipv6_from_xaddrs(xs)
            if hit:
                return hit
        blob = md.get("wsdd_addresses")
        if isinstance(blob, str) and blob.strip():
            for part in re.split(r"[\s,;]+", blob.strip()):
                if "fe80:" in part.lower():
                    hit = _first_fe80_literal(part)
                    if hit:
                        return hit
    return None


def strip_detail_fields_covered_by_overview(
    fields: list[tuple[str, str]],
    overview: dict[str, str],
) -> list[tuple[str, str]]:
    """Drop protocol rows that duplicate the Overview tab (IP, type, location, display name)."""
    ip_ov = (overview.get("ip") or "").strip()
    type_ov = (overview.get("type") or "").strip()
    loc_ov = (overview.get("location") or "").strip()
    name_ov = (overview.get("name") or "").strip()
    out: list[tuple[str, str]] = []
    for k, v in fields:
        nk = str(k).strip().lower()
        vs = (v or "").strip() if isinstance(v, str) else str(v).strip()
        if nk == "ip" and vs == ip_ov:
            continue
        if nk == "type" and vs == type_ov:
            continue
        if nk == "location" and vs == loc_ov:
            continue
        if nk in {"friendly name", "hostname"} and vs == name_ov:
            continue
        if nk == "server" and vs.rstrip(".").lower() == name_ov.rstrip(".").lower():
            continue
        out.append((k, v))
    return out


def _last_seen_text(device: Device) -> str:
    metadata = device.metadata if isinstance(device.metadata, dict) else {}
    if isinstance(device.last_seen, datetime):
        try:
            return device.last_seen.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(device.last_seen)
    return _value_or_unavailable(metadata.get("last_seen"))


def _last_seen_row_sort_key(text: object) -> tuple[float, str]:
    """Numeric time for comparing detail rows; unknown formats sort oldest."""
    s = "" if text is None else str(text).strip()
    if not s or s.lower() == "unavailable":
        return (float("-inf"), s)
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return (dt.timestamp(), s)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (dt.timestamp(), s)
    except Exception:
        return (float("-inf"), s)


def dedupe_last_seen_in_fields(fields: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Collapse multiple ``Last seen`` rows to the chronologically latest."""
    rows_ls: list[tuple[str, str]] = []
    rest: list[tuple[str, str]] = []
    for k, v in fields:
        if str(k).strip().lower() == "last seen":
            rows_ls.append((k, v))
        else:
            rest.append((k, v))
    if len(rows_ls) <= 1:
        return fields
    best = max(rows_ls, key=lambda kv: _last_seen_row_sort_key(kv[1])[0])
    rest.append(best)
    return rest


def _best_last_seen_among_devices(devs: list[Device]) -> str:
    """Pick newest ``Last seen`` text across merged protocol rows."""
    scored: list[tuple[float, str]] = []
    for d in devs:
        if isinstance(d.last_seen, datetime):
            try:
                s = _last_seen_text(d)
                scored.append((d.last_seen.timestamp(), s))
                continue
            except Exception:
                pass
        s = _last_seen_text(d)
        scored.append((_last_seen_row_sort_key(s)[0], s))
    if not scored:
        return ""
    return max(scored, key=lambda x: x[0])[1]


def _truncate(s: str, max_len: int) -> str:
    t = (s or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def build_wsd_family_detail_fields(*devices: Device | None) -> list[tuple[str, str]]:
    """Minimal rows for WSD / wsdd / NMB-only bundles (identity stays on Overview)."""
    devs = [d for d in devices if d is not None]
    if not devs:
        return []
    rows: list[tuple[str, str]] = []
    for d in devs:
        m = resolve_mac_for_device(d)
        if m:
            rows.append(("MAC address", _value_or_unavailable(m)))
            break
    txt = _best_last_seen_among_devices(devs)
    if not txt or str(txt).strip().lower() == "unavailable":
        return rows
    rows.append((_("Last seen"), txt))
    return rows


def merge_ssdp_mdns_detail_fields(
    ssdp_fields: list[tuple[str, str]],
    mdns_fields: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Merge first-tab rows for SSDP + mDNS: SSDP wins on duplicate keys; mDNS-only rows are appended."""

    def _norm_key(key: str) -> str:
        return str(key).strip().lower()

    skip_port_keys = frozenset({"port", "ports"})
    ssdp_fields = [(k, v) for k, v in ssdp_fields if _norm_key(k) not in skip_port_keys]
    mdns_fields = [(k, v) for k, v in mdns_fields if _norm_key(k) not in skip_port_keys]
    # Avoid duplicate Type row when merging SSDP + mDNS (SSDP row kept).
    mdns_fields = [(k, v) for k, v in mdns_fields if _norm_key(k) != "type"]

    summary_norm_labels = summary_field_labels_norm(cached_mdns_rules())

    ssdp_keys = {_norm_key(k) for k, _v in ssdp_fields}
    mdns_fallbacks_norm: dict[str, tuple[str, str]] = {}
    mdns_for_merge: list[tuple[str, str]] = []
    for key, value in mdns_fields:
        nk = _norm_key(key)
        if nk in summary_norm_labels and nk in ssdp_keys:
            if nk not in mdns_fallbacks_norm:
                mdns_fallbacks_norm[nk] = (key, value)
            continue
        mdns_for_merge.append((key, value))

    prefer_ssdp_only = {"ip", "port", "ports", "location", "last seen"}
    out: list[tuple[str, str]] = list(ssdp_fields)
    for key, value in mdns_for_merge:
        nk = _norm_key(key)
        if nk in prefer_ssdp_only and nk in ssdp_keys:
            continue
        if nk in ssdp_keys:
            out.append((f"{key} (mDNS)", value))
        else:
            out.append((key, value))

    if not mdns_fallbacks_norm:
        return dedupe_last_seen_in_fields(out)
    patched: list[tuple[str, str]] = []
    for ok, ov in out:
        nk = _norm_key(ok)
        slot = mdns_fallbacks_norm.get(nk)
        if slot is None or _detail_field_usable(ov):
            patched.append((ok, ov))
            continue
        _fk, fv = slot
        patched.append((ok, fv) if _detail_field_usable(fv) else (ok, ov))
    return dedupe_last_seen_in_fields(patched)
