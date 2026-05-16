# File device_details_view.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Build device details view-model from a merged ``DeviceBundle`` (GTK/Qt shared)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from model.device import Device
from utils.details_payload import (
    build_mdns_payload,
    build_ssdp_payload,
    build_wsd_family_detail_fields,
    collect_link_local_ipv6,
    dedupe_last_seen_in_fields,
    filter_visible_detail_fields,
    format_device_ip_for_details,
    format_device_type_for_details,
    merge_ssdp_mdns_detail_fields,
    strip_detail_fields_covered_by_overview,
    with_aggregate_ports_field,
)
from utils.device_bundles import DeviceBundle, bundle_location_label


def effective_ssdp_device(bundle: DeviceBundle) -> Device | None:
    if bundle.ssdp_device is not None:
        return bundle.ssdp_device
    mdns = bundle.mdns_device
    if mdns is None or not isinstance(mdns.metadata, dict):
        return None
    mdns_meta = mdns.metadata
    xml_fields = mdns_meta.get("xml_fields")
    if not isinstance(xml_fields, dict) or not xml_fields:
        return None
    if not any(
        isinstance(xml_fields.get(k), str) and xml_fields.get(k).strip()
        for k in (
            "friendlyName",
            "deviceType",
            "UDN",
            "modelName",
            "manufacturer",
            "services_description",
        )
    ):
        return None
    synth_meta = dict(mdns_meta)
    synth_meta["xml_fields"] = dict(xml_fields)
    return Device(
        name=mdns.name,
        ip=mdns.ip,
        port=mdns.port,
        type=mdns.type,
        category=mdns.category,
        source="ssdp",
        url=mdns.url,
        metadata=synth_meta,
        last_seen=mdns.last_seen,
        online=mdns.online,
        monitored=mdns.monitored,
        icon=mdns.icon,
    )


@dataclass(slots=True)
class DeviceDetailsViewModel:
    title: str
    overview: dict[str, str]
    fields: list[tuple[str, str]]
    services_records: list[tuple[str, str, str]] | None = None
    mdns_service_sections: list[dict[str, Any]] | None = None
    raw_content: str | None = None
    raw_xml_location: str | None = None
    details_field_rule_map: dict[str, str] = field(default_factory=dict)
    endpoint_source: str = ""
    endpoint_ip: str = ""
    endpoint_port: int = 0
    url_override: str | None = None
    custom_command: str | None = None
    device_commands: list[dict] | None = None


def _metadata_str(devices: list[Device], key: str) -> str | None:
    for dev in devices:
        md = dev.metadata if isinstance(dev.metadata, dict) else {}
        val = md.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def build_device_details_view_model(
    bundle: DeviceBundle,
    *,
    no_location_label: str,
) -> DeviceDetailsViewModel:
    ipv6 = collect_link_local_ipv6(
        bundle.wsdd_device,
        bundle.wsd_device,
        bundle.mdns_device,
        effective_ssdp_device(bundle),
        bundle.nmb_device,
    )
    overview = {
        "name": bundle.name or "",
        "ip": format_device_ip_for_details(bundle.primary),
        "location": bundle_location_label(bundle, no_location_label=no_location_label),
        "type": format_device_type_for_details(bundle.primary),
        "ipv6_link_local": ipv6 or "",
    }
    ssdp = effective_ssdp_device(bundle)
    mdns = bundle.mdns_device
    raw_xml: str | None = None
    xml_location: str | None = None
    services_list: list[tuple[str, str, str]] | None = None
    troubleshooting_fields: list[tuple[str, str]] | None = None
    mdns_sections: list[dict[str, Any]] | None = None
    details_field_rule_map: dict[str, str] = {}

    if ssdp is not None and mdns is not None:
        f_ssdp, raw_xml, services_list, troubleshooting_fields, xml_location = build_ssdp_payload(ssdp)
        f_mdns, mdns_sections = build_mdns_payload(mdns)
        fields = with_aggregate_ports_field(
            merge_ssdp_mdns_detail_fields(f_ssdp, f_mdns), ssdp, mdns
        )
        details_field_rule_map = {
            "Friendly name": "xml:friendlyName",
            "Information": "meta:information",
            "Hostname (mDNS)": "meta:hostname",
            "Server (mDNS)": "meta:server",
        }
    elif ssdp is not None:
        fields, raw_xml, services_list, troubleshooting_fields, xml_location = build_ssdp_payload(ssdp)
        fields = with_aggregate_ports_field(fields, ssdp)
        details_field_rule_map = {
            "Friendly name": "xml:friendlyName",
            "Information": "meta:information",
            "Model": "xml:modelName",
            "Manufacturer": "xml:manufacturer",
        }
    elif mdns is not None:
        fields, mdns_sections = build_mdns_payload(mdns)
        fields = with_aggregate_ports_field(fields, mdns)
        details_field_rule_map = {
            "Hostname": "meta:hostname",
            "Server": "meta:server",
            "Information": "meta:information",
        }
    else:
        fields = build_wsd_family_detail_fields(
            bundle.wsdd_device,
            bundle.wsd_device,
            bundle.nmb_device,
        )
        details_field_rule_map = {}

    if troubleshooting_fields:
        fields.extend(troubleshooting_fields)

    fields = filter_visible_detail_fields(
        dedupe_last_seen_in_fields(
            strip_detail_fields_covered_by_overview(fields, overview)
        )
    )

    devices = bundle.devices
    return DeviceDetailsViewModel(
        title=f"{bundle.name}",
        overview=overview,
        fields=fields,
        services_records=services_list,
        mdns_service_sections=mdns_sections,
        raw_content=raw_xml,
        raw_xml_location=xml_location,
        details_field_rule_map=details_field_rule_map,
        endpoint_source=bundle.primary.source,
        endpoint_ip=bundle.primary.ip,
        endpoint_port=int(bundle.primary.port),
        url_override=_metadata_str(devices, "url_override"),
        custom_command=_metadata_str(devices, "custom_command"),
        device_commands=_metadata_list_dict(devices, "device_commands"),
    )


def _metadata_list_dict(devices: list[Device], key: str) -> list[dict] | None:
    for dev in devices:
        md = dev.metadata if isinstance(dev.metadata, dict) else {}
        cmds = md.get(key)
        if isinstance(cmds, list) and cmds:
            return [c for c in cmds if isinstance(c, dict)]
    return None
