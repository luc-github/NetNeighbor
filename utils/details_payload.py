"""Build protocol detail payloads from normalized metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from model.device import Device
from utils.mdns_rules import cached_mdns_rules, summary_field_labels_norm, summary_rows_from_rules


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
    for i, (k, _) in enumerate(cleaned):
        if _nk(k) == "ip":
            insert_idx = i + 1
            break
    return cleaned[:insert_idx] + [("Ports", aggregate_ports_display(*devices))] + cleaned[insert_idx:]


def _value_or_unavailable(value) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, str) and not value.strip():
        return "unavailable"
    return str(value)


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
        ("IP", device.ip),
        ("Ports", aggregate_ports_display(device)),
        ("Location", _value_or_unavailable(metadata.get("user_location"))),
        ("Last seen", last_seen_text),
        ("Friendly name", _value_or_unavailable(xml_fields.get("friendlyName"))),
        ("Information", _value_or_unavailable(metadata.get("information"))),
        ("Manufacturer", _value_or_unavailable(xml_fields.get("manufacturer"))),
        ("Manufacturer URL", _value_or_unavailable(xml_fields.get("manufacturerURL"))),
        ("Model", _value_or_unavailable(xml_fields.get("modelName"))),
        ("Model URL", _value_or_unavailable(xml_fields.get("modelURL"))),
    ]
    xml_location = metadata.get("location")
    xml_location_norm = xml_location if isinstance(xml_location, str) else None
    raw_xml = xml_data if isinstance(xml_data, str) and xml_data.strip() else None
    troubleshooting_fields: list[tuple[str, str]] = [
        ("Serial number", _value_or_unavailable(xml_fields.get("serialNumber"))),
        (
            "MAC address",
            _value_or_unavailable(
                metadata.get("mac")
                or metadata.get("mac_address")
                or metadata.get("MAC")
                or metadata.get("macAddress")
                or xml_fields.get("mac")
            ),
        ),
        (
            "Unique identifier",
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

    def _norm_name(value) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.strip().rstrip(".").lower()

    hostname_norm = _norm_name(hostname_raw)
    server_norm = _norm_name(server_raw)

    if isinstance(device.last_seen, datetime):
        try:
            last_seen_text = device.last_seen.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            last_seen_text = str(device.last_seen)
    else:
        last_seen_text = _value_or_unavailable(device.last_seen)
    fields = [
        ("IP", device.ip),
        ("Ports", aggregate_ports_display(device)),
        ("Location", _value_or_unavailable(metadata.get("user_location"))),
        ("Last seen", last_seen_text),
        ("Hostname", _value_or_unavailable(hostname_raw)),
    ]

    for label, raw in summary_rows_from_rules(metadata):
        fields.append((label, _value_or_unavailable(raw)))

    if server_norm and server_norm != hostname_norm:
        fields.append(("Server", _value_or_unavailable(server_raw)))

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

    summary_norm_labels = summary_field_labels_norm(cached_mdns_rules())

    ssdp_keys = {_norm_key(k) for k, _ in ssdp_fields}
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
        return out
    patched: list[tuple[str, str]] = []
    for ok, ov in out:
        nk = _norm_key(ok)
        slot = mdns_fallbacks_norm.get(nk)
        if slot is None or _detail_field_usable(ov):
            patched.append((ok, ov))
            continue
        _fk, fv = slot
        patched.append((ok, fv) if _detail_field_usable(fv) else (ok, ov))
    return patched
