"""Build protocol detail payloads from normalized metadata."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from model.device import Device


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
        ("Port", str(device.port)),
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


def build_mdns_payload(
    device: Device,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str, str]] | None]:
    metadata = device.metadata
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
        ("Last seen", last_seen_text),
        ("Hostname", _value_or_unavailable(hostname_raw)),
    ]

    # In DNS-SD, hostname and server/target often match. Avoid redundancy when they are identical.
    if server_norm and server_norm != hostname_norm:
        fields.append(("Server", _value_or_unavailable(server_raw)))

    services_records: list[tuple[str, str, str]] | None = None
    services_meta = metadata.get("services")
    if isinstance(services_meta, list) and services_meta:
        services_records = []
        for idx, svc in enumerate(services_meta):
            if not isinstance(svc, dict):
                continue
            svc_type = svc.get("service") or metadata.get("service")
            port = svc.get("port")
            if port is None:
                port = device.port
            target = svc.get("server") or svc.get("hostname") or server_raw or hostname_raw
            target_str = _value_or_unavailable(target)
            svc_type_str = _value_or_unavailable(svc_type)
            services_records.append((svc_type_str, target_str, str(port)))
        if not services_records:
            services_records = None
    else:
        # Fallback to a single service from legacy fields.
        svc_type = metadata.get("service")
        if svc_type or device.port:
            services_records = [
                (_value_or_unavailable(svc_type), device.ip, str(device.port))
            ]
    txt_records: list[tuple[str, str]] = []
    txt_data = metadata.get("txt")
    if isinstance(txt_data, dict):
        txt_records = [(str(key), str(value)) for key, value in sorted(txt_data.items(), key=lambda item: item[0])]
    elif isinstance(txt_data, list):
        txt_records = [(f"item_{index + 1}", str(value)) for index, value in enumerate(txt_data)]
    elif isinstance(txt_data, str) and txt_data.strip():
        txt_records = [("record", txt_data)]
    else:
        txt_records = [("unavailable", "unavailable")]
    return fields, txt_records, services_records

