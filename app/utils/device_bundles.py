# File device_bundles.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Merge discovery rows by host (same logic as ``ui/device_list``; no GTK imports)."""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from model.device import Device
from utils.discovery_config import (
    information_precedence_rank,
    information_precedence_role_for_device_source,
)
from utils.discovery_identity import upnp_identity_from_udn, upnp_identity_from_usn, uuid_urn_if_present
from utils.location_label import is_plausible_room_location
from utils.neighbor_mac import lookup_mac_from_neighbor_cache

_LOG = logging.getLogger(__name__)

_SOURCE_PRIMARY_TIEBREAK = {"ssdp": 0, "wsdd": 1, "wsd": 2, "nmb": 3, "mdns": 4}
_PRIMARY_RANK_SLACK_FOR_NAME = 2

_WSD_SYNTHETIC_DISPLAY_RE = re.compile(
    r"^ws[d]?[\s\-·∙]+[0-9a-f]{6,}$",
    re.IGNORECASE,
)


def weak_bundle_display_name(device: Device | None) -> bool:
    """True if the row label is too generic to prefer over another protocol's hostname."""
    if device is None:
        return True
    n = (getattr(device, "name", None) or "").strip()
    if not n:
        return True
    low = n.lower()
    if low == "wsd host":
        return True
    if low.startswith("wsd ·") or low.startswith("wsd \u00b7"):
        return True
    if _WSD_SYNTHETIC_DISPLAY_RE.match(n.strip()):
        return True
    if len(n) <= 1:
        return True
    try:
        ipaddress.ip_address(n.strip("[]"))
        return True
    except ValueError:
        pass
    return False


def bundle_row_debug_line(
    dev: Device,
    *,
    rank: int,
    role: str,
    tie: int,
    weak: bool,
) -> str:
    return (
        f"{dev.source}@{dev.ip}:{dev.port} name={dev.name!r} role={role} "
        f"precedence_index={rank} tiebreak={tie} weak_name={weak}"
    )


@dataclass(slots=True)
class DeviceBundle:
    ip: str
    port: int
    primary: Device
    ssdp_device: Device | None = None
    mdns_device: Device | None = None
    wsd_device: Device | None = None
    wsdd_device: Device | None = None
    nmb_device: Device | None = None

    @property
    def name(self) -> str:
        return self.primary.name

    @property
    def category(self) -> str:
        return self.primary.category

    @property
    def type(self) -> str:
        return self.primary.type

    @property
    def icon(self) -> str | None:
        return self.primary.icon

    @property
    def online(self) -> bool:
        return any(device.online for device in self.devices)

    @property
    def monitored(self) -> bool:
        return any(getattr(device, "monitored", False) for device in self.devices)

    @property
    def hidden(self) -> bool:
        return any(getattr(device, "hidden", False) for device in self.devices)

    @property
    def devices(self) -> list[Device]:
        unique: dict[str, Device] = {}
        for device in (self.mdns_device, self.ssdp_device, self.wsdd_device, self.wsd_device, self.nmb_device, self.primary):
            if device is not None:
                unique[device.key] = device
        return list(unique.values())


def bundle_location_label(bundle: DeviceBundle, *, no_location_label: str) -> str:
    for device in bundle.devices:
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        location = metadata.get("user_location")
        if isinstance(location, str) and location.strip():
            s = location.strip()
            if is_plausible_room_location(s):
                return s
    return no_location_label


def protocol_row_preference_rank(device: Device | None, information_precedence: Sequence[str]) -> int:
    if device is None:
        return 99
    role = information_precedence_role_for_device_source(device.source or "")
    return information_precedence_rank(list(information_precedence), role)


def normalize_mac_for_bundle_merge(raw: str) -> str:
    t = (raw or "").strip().lower().replace("-", ":")
    parts = t.split(":")
    if len(parts) != 6:
        return ""
    if not all(len(p) == 2 and all(c in "0123456789abcdef" for c in p) for p in parts):
        return ""
    return ":".join(parts)


def extract_mac(metadata: dict) -> str:
    if not isinstance(metadata, dict):
        return ""
    xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
    txt_fields = metadata.get("txt") if isinstance(metadata.get("txt"), dict) else {}
    candidates = [
        xml_fields.get("mac"),
        metadata.get("mac"),
        metadata.get("mac_address"),
        metadata.get("MAC"),
        metadata.get("macAddress"),
        txt_fields.get("mac"),
        txt_fields.get("macaddress"),
        txt_fields.get("mac_address"),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def extract_uid(metadata: dict) -> str:
    if not isinstance(metadata, dict):
        return ""
    xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
    txt_fields = metadata.get("txt") if isinstance(metadata.get("txt"), dict) else {}

    usn = metadata.get("usn")
    if isinstance(usn, str) and usn.strip():
        return upnp_identity_from_usn(usn)

    wsd_epr = metadata.get("wsd_epr")
    if isinstance(wsd_epr, str) and wsd_epr.strip():
        u = uuid_urn_if_present(wsd_epr)
        if u:
            return u
    wsdd_uri = metadata.get("wsdd_uri")
    if isinstance(wsdd_uri, str) and wsdd_uri.strip():
        u = uuid_urn_if_present(wsdd_uri)
        if u:
            return u

    udn_raw = xml_fields.get("UDN")
    if isinstance(udn_raw, str) and udn_raw.strip():
        hit = upnp_identity_from_udn(udn_raw)
        if hit:
            return hit

    candidates = [
        xml_fields.get("UDN"),
        metadata.get("udn"),
        txt_fields.get("uuid"),
        txt_fields.get("udn"),
        txt_fields.get("id"),
        txt_fields.get("deviceid"),
        txt_fields.get("device_id"),
        txt_fields.get("serial"),
        txt_fields.get("serialnumber"),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def mac_for_bundle_merge(device: Device, metadata: dict) -> str:
    m = extract_mac(metadata)
    n = normalize_mac_for_bundle_merge(m) if m else ""
    if n:
        return n
    hit = lookup_mac_from_neighbor_cache(str(getattr(device, "ip", "") or "").strip())
    return normalize_mac_for_bundle_merge(hit) if hit else ""


def device_host_bundle_keys(device: Device) -> list[str]:
    if device.source not in {"mdns", "ssdp", "wsd", "wsdd", "nmb"}:
        return []
    metadata = device.metadata if isinstance(device.metadata, dict) else {}
    ordered: list[str] = []
    mac = mac_for_bundle_merge(device, metadata)
    if mac:
        ordered.append(f"mac:{mac}")
    uid = extract_uid(metadata)
    if uid:
        ordered.append(f"uid:{uid}")
    ip = str(device.ip).strip()
    if ip and ip != "0.0.0.0":
        ordered.append(f"ip:{ip}")
    seen: set[str] = set()
    deduped: list[str] = []
    for key in ordered:
        if key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def choose_bundle_primary(bundle: DeviceBundle, information_precedence: Sequence[str]) -> Device:
    candidates: list[Device] = []
    for d in (bundle.ssdp_device, bundle.wsdd_device, bundle.wsd_device, bundle.nmb_device, bundle.mdns_device):
        if d is not None:
            candidates.append(d)
    if not candidates:
        return bundle.primary
    if len(candidates) == 1:
        return candidates[0]

    def _sort_key(d: Device) -> tuple[int, int]:
        src = (d.source or "").strip().lower()
        return (
            protocol_row_preference_rank(d, information_precedence),
            _SOURCE_PRIMARY_TIEBREAK.get(src, 9),
        )

    ordered = sorted(candidates, key=_sort_key)
    top = ordered[0]

    if bundle.nmb_device is not None and not weak_bundle_display_name(bundle.nmb_device):
        if top.source in {"wsd", "wsdd"} and weak_bundle_display_name(top):
            return bundle.nmb_device

    best_rank = protocol_row_preference_rank(ordered[0], information_precedence)
    slack = _PRIMARY_RANK_SLACK_FOR_NAME
    for d in ordered:
        dr = protocol_row_preference_rank(d, information_precedence)
        if dr > best_rank + slack:
            break
        if not weak_bundle_display_name(d):
            return d
    return ordered[0]


def _descriptor_richness(dev: Device | None) -> tuple[int, int, int]:
    """Rank a device by how much descriptor data it carries (raw XML, xml_fields, iconURL)."""
    if dev is None:
        return (0, 0, 0)
    md = dev.metadata if isinstance(dev.metadata, dict) else {}
    xf = md.get("xml_fields") if isinstance(md.get("xml_fields"), dict) else {}
    has_xml = 1 if isinstance(md.get("xml"), str) and md.get("xml").strip() else 0
    icon = xf.get("iconURL")
    has_icon = 1 if isinstance(icon, str) and icon.strip() else 0
    return (has_xml, len(xf), has_icon)


def _richer_descriptor_device(existing: Device | None, candidate: Device) -> Device:
    """Pick the richer of two same-source devices that map to one bundle.

    A host can expose several SSDP responses for the same identity — e.g. Sonos returns an
    enriched root descriptor AND a bare "SSDP Device" (XML pending). Whichever is processed last
    must not blank out the descriptor: keep the one carrying real XML / xml_fields / iconURL so the
    SSDP detail tab and device icon survive. Ties favour the newer (candidate) for freshness.
    """
    if existing is None:
        return candidate
    return candidate if _descriptor_richness(candidate) >= _descriptor_richness(existing) else existing


def build_device_bundles(
    devices: list[Device],
    information_precedence: Sequence[str],
) -> list[DeviceBundle]:
    bundles_by_id: dict[str, DeviceBundle] = {}
    host_to_bundle_id: dict[str, str] = {}
    order: list[str] = []
    for device in devices:
        bundle_id = f"endpoint:{device.ip}:{device.port}"
        bundle_keys = device_host_bundle_keys(device)
        for key in bundle_keys:
            existing = host_to_bundle_id.get(key)
            if existing is not None:
                bundle_id = existing
                break
        bundle = bundles_by_id.get(bundle_id)
        if bundle is None:
            bundle = DeviceBundle(ip=device.ip, port=device.port, primary=device)
            bundles_by_id[bundle_id] = bundle
            order.append(bundle_id)

        if device.source == "mdns":
            bundle.mdns_device = _richer_descriptor_device(bundle.mdns_device, device)
        elif device.source == "ssdp":
            bundle.ssdp_device = _richer_descriptor_device(bundle.ssdp_device, device)
        elif device.source == "wsd":
            bundle.wsd_device = device
        elif device.source == "wsdd":
            bundle.wsdd_device = device
        elif device.source == "nmb":
            bundle.nmb_device = device
        bundle.primary = choose_bundle_primary(bundle, information_precedence)

        _LOG.debug(
            "bundle_merge attach source=%s endpoint=%s:%s merge_keys=%s name=%r",
            device.source,
            device.ip,
            device.port,
            bundle_keys,
            device.name,
        )

        for key in bundle_keys:
            host_to_bundle_id[key] = bundle_id
    ordered = [bundles_by_id[key] for key in order]
    for bundle in ordered:
        bundle.ip = bundle.primary.ip
        bundle.port = int(bundle.primary.port)
    return ordered


def bundle_remote_icon_hint(dev: Device | None) -> str:
    """Token that changes when a device's remote-icon source appears.

    Captures both attachment (None vs present) and descriptor enrichment: a row first attached
    generic (e.g. SSDP fast-emit, XML pending) gains ``iconURL`` only once its descriptor is
    parsed. If the fingerprint tracked only ``device is not None``, that late enrichment would not
    flip it — so the bundle would not re-render and the remote icon would never be fetched (it
    stays the generic type icon). Including the icon URL here closes that gap.
    """
    if dev is None:
        return ""
    md = dev.metadata if isinstance(dev.metadata, dict) else {}
    xf = md.get("xml_fields") if isinstance(md.get("xml_fields"), dict) else {}
    icon = xf.get("iconURL") or md.get("iconURL") or ""
    return f"{1 if xf else 0}:{str(icon).strip()}"


def bundle_snapshot_ui_fingerprint(bundles: Sequence[DeviceBundle]) -> tuple:
    """Fingerprint of merged bundles (protocol attachment + primary row) for Qt UI refresh."""
    rows: list[tuple] = []
    for b in sorted(bundles, key=lambda x: (str(x.ip), int(x.port or 0), x.primary.key)):
        p = b.primary
        rows.append(
            (
                str(b.ip).strip(),
                int(b.port or 0),
                p.key,
                (p.source or "").strip().lower(),
                (p.name or "").strip(),
                (p.type or "unknown").strip().lower(),
                bool(p.online),
                bundle_remote_icon_hint(b.ssdp_device),
                bundle_remote_icon_hint(b.mdns_device),
                bundle_remote_icon_hint(b.wsd_device),
                bundle_remote_icon_hint(b.wsdd_device),
                bundle_remote_icon_hint(b.nmb_device),
            )
        )
    return tuple(rows)


def apply_bundle_category_filter(
    bundles: list[DeviceBundle],
    filter_key: str | None,
    *,
    no_location_label: str,
) -> list[DeviceBundle]:
    if not filter_key:
        return list(bundles)
    if isinstance(filter_key, str) and filter_key.startswith("location:"):
        location_value = filter_key.split(":", 1)[1]
        if location_value == "__none__":
            return [
                b for b in bundles if bundle_location_label(b, no_location_label=no_location_label) == no_location_label
            ]
        return [
            b for b in bundles if bundle_location_label(b, no_location_label=no_location_label) == location_value
        ]
    cf = filter_key
    out: list[DeviceBundle] = []
    for bundle in bundles:
        slug = (bundle.primary.type or "unknown").strip().lower()
        if slug == cf.strip().lower():
            out.append(bundle)
        elif bundle.category == cf:
            out.append(bundle)
    return out
