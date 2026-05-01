"""Orchestrates all protocol providers and keeps a simple device cache."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import ipaddress
import logging
from typing import Literal

from discovery.base import BaseDiscovery
from discovery.mdns import MDNSDiscovery
from discovery.ssdp import SSDPDiscovery
from model.device import Device
from utils.location_label import is_plausible_room_location

PresenceTransitionKind = Literal["online", "offline"]
PresenceTransitionHook = Callable[[Device, PresenceTransitionKind], None]
_IDENTITY_HOLD_SECONDS = 3.0


class DiscoveryManager:
    def __init__(self, demo_mode: bool = False) -> None:
        self._logger = logging.getLogger(__name__)
        self._ssdp_logger = logging.getLogger(f"{__name__}.ssdp")
        self._mdns_logger = logging.getLogger(f"{__name__}.mdns")
        self._protocols: list[BaseDiscovery] = [SSDPDiscovery(), MDNSDiscovery()]
        self._devices: dict[str, Device] = {}
        self._arrival_sequence = 0
        self._listeners: list[Callable[[list[Device]], None]] = []
        self._presence_hooks: list[PresenceTransitionHook] = []
        self._type_overrides: dict[str, str] = {}
        self._name_overrides: dict[str, str] = {}
        self._location_overrides: dict[str, str] = {}
        self._monitored_overrides: dict[str, bool] = {}
        self._last_seen_overrides: dict[str, str] = {}
        self._identity_pending: dict[str, tuple[datetime, Device]] = {}
        self._demo_mode = demo_mode
        self._location_prefs_need_reapply = False
        self._location_prefs_dirty_callback: Callable[[], None] | None = None

        for protocol in self._protocols:
            protocol.set_callback(self._on_protocol_event)

    def set_location_prefs_dirty_callback(self, callback: Callable[[], None] | None) -> None:
        self._location_prefs_dirty_callback = callback

    def add_listener(self, callback: Callable[[list[Device]], None]) -> None:
        self._listeners.append(callback)
        callback(self.devices)

    def register_presence_transition_hook(self, hook: PresenceTransitionHook) -> None:
        """Notify when a device becomes reachable or unreachable (same identity row).

        Fires only on transitions: newly online (including back from offline), or offline from
        online. Silent for first-seen rows that are already offline (e.g. restored monitored
        ghosts). Plugins should treat callbacks as potentially running on a discovery thread —
        marshal to the UI thread before touching GTK.

        Reserved for future automation / plugins; core code does not register hooks today.
        """
        self._presence_hooks.append(hook)

    def unregister_presence_transition_hook(self, hook: PresenceTransitionHook) -> None:
        try:
            self._presence_hooks.remove(hook)
        except ValueError:
            pass

    @property
    def devices(self) -> list[Device]:
        return sorted(self._devices.values(), key=lambda device: (device.category, device.name.lower()))

    def start(self) -> None:
        self._logger.info("Starting discovery protocols: %s", [p.source for p in self._protocols])
        for protocol in self._protocols:
            protocol.start()

    def stop(self) -> None:
        self._logger.info("Stopping discovery protocols")
        for protocol in self._protocols:
            protocol.stop()
        self._identity_pending.clear()

    def refresh(self) -> None:
        self._logger.debug("Manual refresh requested")
        for protocol in self._protocols:
            protocol.refresh()

    def _device_event_logger(self, source: str) -> logging.Logger:
        if source == "ssdp":
            return self._ssdp_logger
        if source == "mdns":
            return self._mdns_logger
        return self._logger

    def add_or_update_device(self, device: Device) -> None:
        sip = str(device.ip).strip()
        if sip in {"", "0.0.0.0"}:
            self._device_event_logger(device.source).debug(
                "Ignoring device with placeholder IP: source=%s name=%s port=%s online=%s",
                device.source,
                device.name,
                device.port,
                device.online,
            )
            return
        if self._should_hold_for_stable_identity(device):
            return

        override_key = self._make_override_key_for_device(device)
        existing_seen = self._last_seen_overrides.get(override_key)
        if not existing_seen:
            existing_seen = self._last_seen_overrides.get(self._make_override_key(device.source, device.ip, device.port))
        if device.online:
            now = datetime.now(timezone.utc)
            device.last_seen = now
            self._last_seen_overrides[override_key] = now.isoformat()
        elif existing_seen:
            try:
                device.last_seen = datetime.fromisoformat(existing_seen)
            except ValueError:
                pass

        existing_key = device.key
        existing = self._devices.get(existing_key)
        if existing is None and device.source == "ssdp":
            existing_key, existing = self._find_existing_ssdp_by_endpoint(device)

        self._apply_type_override(device)
        self._apply_name_override(device)
        self._apply_location_override(device)
        self._apply_monitored_override(device)

        prev_online: bool | None = existing.online if existing is not None else None

        if existing is not None:
            # Preserve user-follow choice across updates.
            device.monitored = existing.monitored
            if device.online is False and existing.last_seen:
                device.last_seen = existing.last_seen
            if device.source == "ssdp":
                self._ssdp_logger.debug(
                    "SSDP merge candidate for %s:%s old_name=%s new_name=%s",
                    device.ip,
                    device.port,
                    existing.name,
                    device.name,
                )
                device.metadata = self._merge_ssdp_metadata(existing.metadata, device.metadata, existing.name, device.name)
                device.name = self._pick_ssdp_name(existing.name, device.name, existing.metadata, device.metadata)
                device.icon = existing.icon or device.icon
                if not device.url and existing.url:
                    device.url = existing.url
                if existing_key != device.key and existing_key in self._devices:
                    del self._devices[existing_key]
                existing_key = device.key

        if not isinstance(device.metadata, dict):
            device.metadata = {}
        existing_arrival = None
        if isinstance(existing, Device) and isinstance(existing.metadata, dict):
            candidate = existing.metadata.get("_arrival_index")
            if isinstance(candidate, int):
                existing_arrival = candidate
        if existing_arrival is None:
            self._arrival_sequence += 1
            existing_arrival = self._arrival_sequence
        device.metadata["_arrival_index"] = existing_arrival
        self._supplement_missing_user_location(device, existing)
        self._devices[existing_key] = device
        self._run_location_reapply_sweep()
        device_log = self._device_event_logger(device.source)
        device_log.info(
            "Device %s: source=%s name=%s ip=%s port=%s type=%s category=%s online=%s",
            "updated" if existing else "added",
            device.source,
            device.name,
            device.ip,
            device.port,
            device.type,
            device.category,
            device.online,
        )
        device_log.debug(
            "Device %s: %s %s:%s [%s] online=%s category=%s",
            "updated" if existing else "added",
            device.source,
            device.ip,
            device.port,
            device.name,
            device.online,
            device.category,
        )
        uloc = device.metadata.get("user_location") if isinstance(device.metadata, dict) else None
        device_log.debug(
            "Device appearance: ip=%s port=%s type=%s icon=%s user_location=%r",
            device.ip,
            device.port,
            device.type,
            device.icon,
            uloc,
        )
        self._emit_presence_hooks_if_transition(device, prev_online, device.online)
        self._notify()

    def _stable_identity_available(self, device: Device) -> bool:
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        return bool(self._extract_uid(metadata) or self._extract_mac(metadata))

    def _pending_identity_key(self, device: Device) -> str:
        return f"{(device.source or '').strip().lower()}:{str(device.ip).strip()}:{int(device.port)}"

    def _should_hold_for_stable_identity(self, device: Device) -> bool:
        """Delay first appearance briefly until UID/MAC shows up, then release."""
        if device.source not in {"mdns", "ssdp"} or not bool(device.online):
            return False
        if self._stable_identity_available(device):
            pending_key = self._pending_identity_key(device)
            if pending_key in self._identity_pending:
                self._identity_pending.pop(pending_key, None)
                self._device_event_logger(device.source).debug(
                    "identity_resolved ip=%s port=%s source=%s",
                    device.ip,
                    device.port,
                    device.source,
                )
            return False

        now = datetime.now(timezone.utc)
        pending_key = self._pending_identity_key(device)
        first_seen, _prev = self._identity_pending.get(pending_key, (now, device))
        self._identity_pending[pending_key] = (first_seen, device)
        if now - first_seen < timedelta(seconds=_IDENTITY_HOLD_SECONDS):
            self._device_event_logger(device.source).debug(
                "identity_pending ip=%s port=%s source=%s held_for_ms=%s",
                device.ip,
                device.port,
                device.source,
                int((now - first_seen).total_seconds() * 1000),
            )
            return True
        self._device_event_logger(device.source).debug(
            "identity_pending_timeout ip=%s port=%s source=%s hold_s=%s",
            device.ip,
            device.port,
            device.source,
            _IDENTITY_HOLD_SECONDS,
        )
        self._identity_pending.pop(pending_key, None)
        return False

    def _emit_presence_hooks_if_transition(self, device: Device, prev_online: bool | None, now_online: bool) -> None:
        # Skip first-seen already-offline rows (monitoring placeholders, etc.).
        if now_online and prev_online is not True:
            transition: PresenceTransitionKind = "online"
        elif prev_online is True and not now_online:
            transition = "offline"
        else:
            return
        if not self._presence_hooks:
            return
        for hook in tuple(self._presence_hooks):
            try:
                hook(device, transition)
            except Exception:
                self._logger.exception("Presence transition hook failed (event=%s)", transition)

    def set_type_overrides(self, overrides: dict[str, str]) -> None:
        normalized: dict[str, str] = {}
        for key, value in overrides.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            value_norm = value.strip().lower()
            if value_norm:
                normalized[key] = value_norm
        self._type_overrides = normalized

    def get_type_overrides(self) -> dict[str, str]:
        return dict(self._type_overrides)

    def set_name_overrides(self, overrides: dict[str, str]) -> None:
        normalized: dict[str, str] = {}
        for key, value in overrides.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            value_norm = value.strip()
            if value_norm:
                normalized[key] = value_norm
        self._name_overrides = normalized

    def get_name_overrides(self) -> dict[str, str]:
        return dict(self._name_overrides)

    def set_location_overrides(self, overrides: dict[str, str]) -> None:
        normalized: dict[str, str] = {}
        for key, value in overrides.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            value_norm = value.strip()
            if value_norm:
                normalized[key] = value_norm
        self._location_overrides = normalized
        changed = False
        for device in self._devices.values():
            before = ""
            metadata = device.metadata if isinstance(device.metadata, dict) else {}
            value = metadata.get("user_location")
            if isinstance(value, str):
                before = value
            self._apply_location_override(device)
            metadata_after = device.metadata if isinstance(device.metadata, dict) else {}
            after_raw = metadata_after.get("user_location")
            after = after_raw if isinstance(after_raw, str) else ""
            if before != after:
                changed = True
        if self._run_location_reapply_sweep():
            changed = True
        if changed:
            self._notify()

    def get_location_overrides(self) -> dict[str, str]:
        return dict(self._location_overrides)

    def set_monitored_overrides(self, overrides: dict[str, bool]) -> None:
        normalized: dict[str, bool] = {}
        for key, value in overrides.items():
            if isinstance(key, str):
                normalized[key] = bool(value)
        self._monitored_overrides = normalized

    def get_monitored_overrides(self) -> dict[str, bool]:
        return dict(self._monitored_overrides)

    def set_last_seen_overrides(self, overrides: dict[str, str]) -> None:
        normalized: dict[str, str] = {}
        for key, value in overrides.items():
            if isinstance(key, str) and isinstance(value, str) and value.strip():
                normalized[key] = value.strip()
        self._last_seen_overrides = normalized

    def get_last_seen_overrides(self) -> dict[str, str]:
        return dict(self._last_seen_overrides)

    def restore_monitored_snapshots(self, snapshots: list[dict]) -> None:
        if not isinstance(snapshots, list):
            return
        changed = False
        for raw in snapshots:
            if not isinstance(raw, dict):
                continue
            source = str(raw.get("source", "unknown")).strip().lower()
            ip = str(raw.get("ip", "")).strip()
            port_raw = raw.get("port", 0)
            try:
                port = int(port_raw)
            except (TypeError, ValueError):
                continue
            if not ip or port <= 0:
                continue
            metadata = raw.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            device = Device(
                name=str(raw.get("name", "Unknown")) or "Unknown",
                ip=ip,
                port=port,
                type=str(raw.get("type", "unknown")) or "unknown",
                category=str(raw.get("category", "Unknown Devices")) or "Unknown Devices",
                source=source or "unknown",
                url=raw.get("url") if isinstance(raw.get("url"), str) else None,
                metadata=metadata,
                online=False,
                monitored=True,
                icon=raw.get("icon") if isinstance(raw.get("icon"), str) else None,
            )
            seen_text = raw.get("last_seen")
            if isinstance(seen_text, str) and seen_text.strip():
                try:
                    device.last_seen = datetime.fromisoformat(seen_text.strip())
                except ValueError:
                    pass
            self._apply_type_override(device)
            self._apply_monitored_override(device)
            if not device.monitored:
                continue
            existing = self._devices.get(device.key)
            if existing is not None and existing.online:
                continue
            self._devices[device.key] = device
            changed = True
        if changed:
            self._notify()

    def set_device_type_override(self, source: str, ip: str, port: int, device_type: str | None) -> None:
        changed = False
        for old_key, existing in list(self._devices.items()):
            if existing.source != source or existing.ip != ip or existing.port != port:
                continue
            preferred_key = self._make_override_key_for_device(existing)
            legacy_key = self._make_legacy_identity_key_for_device(existing)
            endpoint_key = self._make_override_key(source, ip, port)
            if device_type is None or not str(device_type).strip() or str(device_type).strip().lower() == "auto":
                self._type_overrides.pop(preferred_key, None)
                if legacy_key:
                    self._type_overrides.pop(legacy_key, None)
                self._type_overrides.pop(endpoint_key, None)
            else:
                if legacy_key:
                    self._type_overrides.pop(legacy_key, None)
                self._type_overrides.pop(endpoint_key, None)
                self._type_overrides[preferred_key] = str(device_type).strip().lower()
            self._apply_type_override(existing)
            new_key = existing.key
            if new_key != old_key:
                self._devices.pop(old_key, None)
                self._devices[new_key] = existing
            changed = True
        if changed:
            self._notify()

    def set_device_name_override(self, source: str, ip: str, port: int, device_name: str | None) -> None:
        changed = False
        for _old_key, existing in list(self._devices.items()):
            if existing.source != source or existing.ip != ip or existing.port != port:
                continue
            preferred_key = self._make_name_override_key_for_device(existing)
            legacy_key = self._make_legacy_identity_key_for_device(existing)
            endpoint_key = self._make_override_key(source, ip, port)
            if device_name is None or not str(device_name).strip():
                self._name_overrides.pop(preferred_key, None)
                if legacy_key:
                    self._name_overrides.pop(legacy_key, None)
                self._name_overrides.pop(endpoint_key, None)
                existing.name = self._default_name_for_device(existing)
            else:
                if legacy_key:
                    self._name_overrides.pop(legacy_key, None)
                self._name_overrides.pop(endpoint_key, None)
                self._name_overrides[preferred_key] = str(device_name).strip()
            self._apply_name_override(existing)
            changed = True
        if changed:
            self._notify()

    def set_device_location_override(self, source: str, ip: str, port: int, location: str | None) -> None:
        changed = False
        for _old_key, existing in list(self._devices.items()):
            if existing.source != source or existing.ip != ip or existing.port != port:
                continue
            preferred_key = self._make_name_override_key_for_device(existing)
            legacy_key = self._make_legacy_identity_key_for_device(existing)
            endpoint_key = self._make_override_key(source, ip, port)
            if location is None or not str(location).strip():
                self._location_overrides.pop(preferred_key, None)
                if legacy_key:
                    self._location_overrides.pop(legacy_key, None)
                self._location_overrides.pop(endpoint_key, None)
            else:
                if legacy_key:
                    self._location_overrides.pop(legacy_key, None)
                self._location_overrides.pop(endpoint_key, None)
                self._location_overrides[preferred_key] = str(location).strip()
            self._apply_location_override(existing)
            changed = True
        if changed:
            self._notify()

    def _merge_ssdp_metadata(self, old_meta: dict, new_meta: dict, old_name: str, new_name: str) -> dict:
        old_meta = old_meta if isinstance(old_meta, dict) else {}
        new_meta = new_meta if isinstance(new_meta, dict) else {}
        merged = dict(old_meta)
        merged.update(new_meta)

        old_xml_fields = old_meta.get("xml_fields") if isinstance(old_meta.get("xml_fields"), dict) else {}
        new_xml_fields = new_meta.get("xml_fields") if isinstance(new_meta.get("xml_fields"), dict) else {}
        old_rank = self._ssdp_profile_rank(old_xml_fields)
        new_rank = self._ssdp_profile_rank(new_xml_fields)
        preferred_xml_fields = old_xml_fields if old_rank >= new_rank else new_xml_fields
        self._ssdp_logger.debug(
            "SSDP profile rank old=%s new=%s selected=%s",
            old_rank,
            new_rank,
            "old" if old_rank >= new_rank else "new",
        )

        merged_xml_fields = dict(old_xml_fields)
        for key, value in new_xml_fields.items():
            if value is None:
                continue
            if isinstance(value, str):
                value_norm = value.strip()
                if not value_norm or value_norm.lower() == "unavailable":
                    continue
                old_value = merged_xml_fields.get(key)
                if not old_value:
                    merged_xml_fields[key] = value_norm
                    continue
                # Keep richer data when two payloads disagree (e.g. Sonos speaker group vs full root description).
                if len(value_norm) > len(str(old_value).strip()):
                    merged_xml_fields[key] = value_norm
            else:
                merged_xml_fields[key] = value

        # Strict preference for the richer/profile-ranked XML fields.
        for strict_key in ("friendlyName", "deviceType", "iconURL", "icons_description"):
            preferred_value = preferred_xml_fields.get(strict_key)
            if isinstance(preferred_value, str) and preferred_value.strip():
                merged_xml_fields[strict_key] = preferred_value.strip()

        merged["xml_fields"] = merged_xml_fields

        # Track alternate names instead of overwriting main identity.
        aliases = merged.get("alternate_names")
        if not isinstance(aliases, list):
            aliases = []
        for candidate in (old_name, new_name):
            if not isinstance(candidate, str):
                continue
            value = candidate.strip()
            if not value or value in aliases:
                continue
            aliases.append(value)
            self._ssdp_logger.debug("SSDP alias tracked: %s", value)
        merged["alternate_names"] = aliases

        # Keep last known information text when a partial payload does not provide it.
        old_info = old_meta.get("information")
        new_info = new_meta.get("information")
        if isinstance(new_info, str) and new_info.strip():
            merged["information"] = new_info.strip()
        elif isinstance(old_info, str) and old_info.strip():
            merged["information"] = old_info.strip()

        old_xml = old_meta.get("xml")
        new_xml = new_meta.get("xml")
        if isinstance(old_xml, str) and isinstance(new_xml, str):
            if old_rank >= new_rank and len(old_xml.strip()) >= len(new_xml.strip()):
                merged["xml"] = old_xml
                self._ssdp_logger.debug("SSDP raw XML preserved from previous richer profile")
        elif isinstance(old_xml, str) and not isinstance(new_xml, str):
            merged["xml"] = old_xml
            self._ssdp_logger.debug("SSDP raw XML preserved because new payload has no XML")

        return merged

    def _ssdp_profile_rank(self, xml_fields: dict) -> int:
        if not isinstance(xml_fields, dict):
            return 0
        device_type = str(xml_fields.get("deviceType", "")).lower()
        score = 0
        if "schemas-upnp-org" in device_type:
            score += 4
        if "smartspeaker-audio" in device_type:
            score -= 1
        if isinstance(xml_fields.get("iconURL"), str) and xml_fields.get("iconURL", "").strip():
            score += 1
        if isinstance(xml_fields.get("manufacturer"), str) and xml_fields.get("manufacturer", "").strip():
            score += 1
        if isinstance(xml_fields.get("modelName"), str) and xml_fields.get("modelName", "").strip():
            score += 1
        return score

    def _pick_ssdp_name(self, old_name: str, new_name: str, old_meta: dict, new_meta: dict) -> str:
        old_name = old_name.strip() if isinstance(old_name, str) else ""
        new_name = new_name.strip() if isinstance(new_name, str) else ""
        if not old_name:
            return new_name or "Unknown"
        if not new_name:
            return old_name

        if old_name.startswith(("SSDP Device ", "Router ", "Media Server ", "Printer ")) and not new_name.startswith(
            ("SSDP Device ", "Router ", "Media Server ", "Printer ")
        ):
            return new_name

        old_fields = old_meta.get("xml_fields") if isinstance(old_meta, dict) and isinstance(old_meta.get("xml_fields"), dict) else {}
        new_fields = new_meta.get("xml_fields") if isinstance(new_meta, dict) and isinstance(new_meta.get("xml_fields"), dict) else {}
        if self._ssdp_profile_rank(new_fields) > self._ssdp_profile_rank(old_fields):
            self._ssdp_logger.debug("SSDP name switched to newer profile name: %s", new_name)
            return new_name
        self._ssdp_logger.debug("SSDP name preserved from previous profile: %s", old_name)
        return old_name

    def _find_existing_ssdp_by_endpoint(self, candidate: Device) -> tuple[str, Device] | tuple[None, None]:
        candidate_mac = self._extract_mac(candidate.metadata)
        for key, item in self._devices.items():
            if item.source != "ssdp":
                continue
            if item.ip != candidate.ip or item.port != candidate.port:
                continue
            existing_mac = self._extract_mac(item.metadata)
            if candidate_mac and existing_mac and candidate_mac != existing_mac:
                self._ssdp_logger.debug(
                    "SSDP endpoint match rejected due to MAC mismatch ip=%s port=%s old=%s new=%s",
                    candidate.ip,
                    candidate.port,
                    existing_mac,
                    candidate_mac,
                )
                continue
            if candidate_mac and not existing_mac:
                self._ssdp_logger.debug(
                    "SSDP endpoint match accepted and upgraded with MAC ip=%s port=%s mac=%s",
                    candidate.ip,
                    candidate.port,
                    candidate_mac,
                )
            if existing_mac and not candidate_mac:
                self._ssdp_logger.debug(
                    "SSDP endpoint match accepted using existing MAC ip=%s port=%s mac=%s",
                    candidate.ip,
                    candidate.port,
                    existing_mac,
                )
                return key, item
            return key, item
        return None, None

    def _extract_mac(self, metadata: dict) -> str:
        if not isinstance(metadata, dict):
            return ""
        xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
        mac = (
            xml_fields.get("mac")
            or metadata.get("mac")
            or metadata.get("mac_address")
            or metadata.get("MAC")
            or metadata.get("macAddress")
        )
        if isinstance(mac, str) and mac.strip():
            return mac.strip().lower()

        def _mac_from_txt(txt: dict) -> str:
            if not isinstance(txt, dict):
                return ""
            for key in ("mac", "hardware-address", "hwaddr", "device-mac", "machine"):
                raw = txt.get(key)
                if isinstance(raw, str) and raw.strip():
                    return raw.strip().lower()
            return ""

        top = _mac_from_txt(metadata.get("txt") if isinstance(metadata.get("txt"), dict) else {})
        if top:
            return top
        services = metadata.get("services")
        if isinstance(services, list):
            for svc in services:
                if not isinstance(svc, dict):
                    continue
                got = _mac_from_txt(svc.get("txt") if isinstance(svc.get("txt"), dict) else {})
                if got:
                    return got
        return ""

    def _apply_type_override(self, device: Device) -> None:
        override_type = self._find_override_value(self._type_overrides, device)
        if not override_type:
            return
        device.type = override_type
        device.category = self._category_for_type(override_type)

    def _apply_name_override(self, device: Device) -> None:
        override_name = self._find_name_override_value(device)
        if not override_name:
            return
        device.name = override_name

    def _clip_loc_log(self, value: str) -> str:
        return value if len(value) <= 120 else value[:117] + "..."

    def _store_location_preference_for_device(self, device: Device, location: str) -> bool:
        """Write identity-based location prefs (same key rules as set_device_location_override)."""
        loc = str(location).strip()
        if not loc or not is_plausible_room_location(loc):
            return False
        preferred_key = self._make_name_override_key_for_device(device)
        legacy_key = self._make_legacy_identity_key_for_device(device)
        endpoint_key = self._make_override_key(device.source, device.ip, device.port)
        if self._location_overrides.get(preferred_key) == loc:
            return False
        if legacy_key:
            self._location_overrides.pop(legacy_key, None)
        self._location_overrides.pop(endpoint_key, None)
        self._location_overrides[preferred_key] = loc
        self._location_prefs_need_reapply = True
        return True

    def _consume_location_prefs_need_reapply(self) -> bool:
        if not self._location_prefs_need_reapply:
            return False
        self._location_prefs_need_reapply = False
        return True

    def _run_location_reapply_sweep(self) -> bool:
        """Re-apply location rules to all cached devices after prefs mutation; persist if needed."""
        any_round = False
        rounds = 0
        while self._consume_location_prefs_need_reapply() and rounds < 8:
            rounds += 1
            any_round = True
            for existing in list(self._devices.values()):
                self._apply_location_override(existing)
        if any_round and self._location_prefs_dirty_callback is not None:
            try:
                self._location_prefs_dirty_callback()
            except Exception:
                self._logger.exception("location_prefs_dirty_callback failed")
        return any_round

    def _apply_location_override(self, device: Device) -> None:
        if not isinstance(device.metadata, dict):
            device.metadata = {}
        dev_log = self._device_event_logger(device.source)
        cached_raw = self._find_location_override_value(device)
        discovered_raw = self._default_location_for_device(device)
        c_norm = cached_raw.strip() if isinstance(cached_raw, str) and cached_raw.strip() else ""
        d_norm = discovered_raw.strip() if isinstance(discovered_raw, str) and discovered_raw.strip() else ""

        chosen = ""

        if c_norm:
            if d_norm and d_norm != c_norm and is_plausible_room_location(d_norm):
                self._store_location_preference_for_device(device, d_norm)
                chosen = d_norm
                dev_log.debug(
                    "Location: discovery overrides cache ip=%s port=%s old=%r new=%r",
                    device.ip,
                    device.port,
                    self._clip_loc_log(c_norm),
                    self._clip_loc_log(d_norm),
                )
            else:
                chosen = c_norm
                dev_log.debug(
                    "Location: prefs cache ip=%s port=%s value=%r",
                    device.ip,
                    device.port,
                    self._clip_loc_log(chosen),
                )
        elif d_norm and is_plausible_room_location(d_norm):
            self._store_location_preference_for_device(device, d_norm)
            chosen = d_norm
            dev_log.debug(
                "Location: auto from discovery (persisted) ip=%s port=%s value=%r",
                device.ip,
                device.port,
                self._clip_loc_log(chosen),
            )
        else:
            dev_log.debug(
                "Location: empty ip=%s port=%s (no RoomName/xml_fields or TXT keys matched)",
                device.ip,
                device.port,
            )

        if chosen:
            device.metadata["user_location"] = chosen
        else:
            device.metadata.pop("user_location", None)

    def _apply_monitored_override(self, device: Device) -> None:
        override_value = self._find_override_value(self._monitored_overrides, device)
        if override_value is not None:
            device.monitored = bool(override_value)

    def _find_override_value(self, store: dict, device: Device):
        preferred_key = self._make_override_key_for_device(device)
        if preferred_key in store:
            return store[preferred_key]
        legacy_key = self._make_legacy_identity_key_for_device(device)
        if legacy_key and legacy_key in store:
            return store[legacy_key]
        endpoint_key = self._make_override_key(device.source, device.ip, device.port)
        if endpoint_key in store:
            return store[endpoint_key]
        hit = self._prefs_entry_same_source_ipv4_any_port(store, device)
        if hit is not None:
            return hit
        return self._prefs_entry_for_host_ip_fallback(store, device)

    def _find_name_override_value(self, device: Device) -> str | None:
        preferred_key = self._make_name_override_key_for_device(device)
        value = self._name_overrides.get(preferred_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        legacy_key = self._make_legacy_identity_key_for_device(device)
        if legacy_key and legacy_key in self._name_overrides:
            value = self._name_overrides.get(legacy_key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        endpoint_key = self._make_override_key(device.source, device.ip, device.port)
        value = self._name_overrides.get(endpoint_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        hit_ep = self._prefs_entry_same_source_ipv4_any_port(self._name_overrides, device)
        if isinstance(hit_ep, str) and hit_ep.strip():
            return hit_ep.strip()
        hit = self._prefs_entry_for_host_ip_fallback(self._name_overrides, device)
        if isinstance(hit, str) and hit.strip():
            return hit.strip()
        return None

    def _find_location_override_value(self, device: Device) -> str | None:
        preferred_key = self._make_name_override_key_for_device(device)
        value = self._location_overrides.get(preferred_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        legacy_key = self._make_legacy_identity_key_for_device(device)
        if legacy_key and legacy_key in self._location_overrides:
            value = self._location_overrides.get(legacy_key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        endpoint_key = self._make_override_key(device.source, device.ip, device.port)
        value = self._location_overrides.get(endpoint_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        hit_ep = self._prefs_entry_same_source_ipv4_any_port(self._location_overrides, device)
        if isinstance(hit_ep, str) and hit_ep.strip():
            return hit_ep.strip()
        hit = self._prefs_entry_for_host_ip_fallback(self._location_overrides, device)
        if isinstance(hit, str) and hit.strip():
            return hit.strip()
        return None

    def _prefs_entry_for_host_ip_fallback(self, store: dict, device: Device):
        """Prefs may use host:ip:… while identity later resolves to host:uid:… / host:mac:… (richer mDNS)."""
        ip_only = str(device.ip).strip()
        if not ip_only or ip_only == "0.0.0.0":
            return None
        host_ip_key = f"host:ip:{ip_only}"
        if self._make_override_key_for_device(device) == host_ip_key:
            return None
        if host_ip_key in store:
            return store[host_ip_key]
        return None

    def _prefs_entry_same_source_ipv4_any_port(self, store: dict, device: Device):
        """Match `mdns:192.168.1.10:8611` when aggregated row moves to `:631`, etc. (IPv4-only)."""
        sip = str(device.ip).strip()
        if sip in {"", "0.0.0.0"}:
            return None
        try:
            if not isinstance(ipaddress.ip_address(sip), ipaddress.IPv4Address):
                return None
        except ValueError:
            return None
        prefix = f"{(device.source or '').strip().lower()}:{sip}:"
        matches: list[str] = []
        for key in store:
            if isinstance(key, str) and key.startswith(prefix):
                tail = key[len(prefix) :]
                if tail.isdigit():
                    matches.append(key)
        if not matches:
            return None

        endpoint_exact = prefix + str(int(device.port))
        if endpoint_exact in store:
            return store.get(endpoint_exact)
        widest = max(matches, key=lambda k: int(k.rsplit(":", 1)[-1]))
        return store.get(widest)

    def _supplement_missing_user_location(self, device: Device, prior_row: Device | None) -> None:
        """Stabilise location when fresh payload lacks room info but cache already had one."""
        if device.source not in {"mdns", "ssdp"}:
            return
        if not isinstance(device.metadata, dict):
            device.metadata = {}
        cur = device.metadata.get("user_location")
        if isinstance(cur, str) and cur.strip():
            return
        sip = str(device.ip).strip()
        if not sip or sip == "0.0.0.0":
            return
        if prior_row is not None and prior_row.source == device.source and prior_row.key == device.key:
            dm = prior_row.metadata if isinstance(prior_row.metadata, dict) else {}
            preserved = dm.get("user_location")
            if isinstance(preserved, str) and preserved.strip():
                ps = preserved.strip()
                if is_plausible_room_location(ps):
                    device.metadata["user_location"] = ps
                    self._device_event_logger(device.source).debug(
                        "Location: %s carry-over from prior row ip=%s value=%r",
                        device.source,
                        sip,
                        ps if len(ps) <= 120 else ps[:117] + "...",
                    )
                    return
        if device.source == "ssdp":
            for row in self._devices.values():
                if row is prior_row:
                    continue
                if row.source != "ssdp" or row.key == device.key:
                    continue
                if str(row.ip).strip() != sip:
                    continue
                dm = row.metadata if isinstance(row.metadata, dict) else {}
                borrowed = dm.get("user_location")
                if isinstance(borrowed, str) and borrowed.strip():
                    bs = borrowed.strip()
                    if not is_plausible_room_location(bs):
                        continue
                    device.metadata["user_location"] = bs
                    self._ssdp_logger.debug(
                        "Location: SSDP borrowed same-ip ip=%s from key=%s value=%r",
                        sip,
                        row.key,
                        bs if len(bs) <= 120 else bs[:117] + "...",
                    )
                    return
            return
        for row in self._devices.values():
            if row is prior_row:
                continue
            if row.source != "mdns" or row.key == device.key:
                continue
            if str(row.ip).strip() != sip:
                continue
            dm = row.metadata if isinstance(row.metadata, dict) else {}
            borrowed = dm.get("user_location")
            if isinstance(borrowed, str) and borrowed.strip():
                bs = borrowed.strip()
                if not is_plausible_room_location(bs):
                    continue
                device.metadata["user_location"] = bs
                self._mdns_logger.debug(
                    "Location: mDNS borrowed same-ip ip=%s from key=%s value=%r",
                    sip,
                    row.key,
                    bs if len(bs) <= 120 else bs[:117] + "...",
                )
                return

    def _make_override_key(self, source: str, ip: str, port: int) -> str:
        return f"{source}:{ip}:{int(port)}"

    def _make_override_key_for_device(self, device: Device) -> str:
        uid = self._extract_uid(device.metadata)
        if uid:
            return f"host:uid:{uid}"
        mac = self._extract_mac(device.metadata)
        if mac:
            return f"host:mac:{mac}"
        ip = str(device.ip).strip()
        if ip and ip != "0.0.0.0":
            return f"host:ip:{ip}"
        return self._make_override_key(device.source, device.ip, device.port)

    def _make_name_override_key_for_device(self, device: Device) -> str:
        return self._make_override_key_for_device(device)

    def _make_legacy_identity_key_for_device(self, device: Device) -> str:
        uid = self._extract_uid(device.metadata)
        if uid:
            return f"{device.source}:uid:{uid}"
        mac = self._extract_mac(device.metadata)
        if mac:
            return f"{device.source}:mac:{mac}"
        return ""

    def _extract_uid(self, metadata: dict) -> str:
        if not isinstance(metadata, dict):
            return ""
        xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
        txt_fields = metadata.get("txt") if isinstance(metadata.get("txt"), dict) else {}
        usn = metadata.get("usn")
        if isinstance(usn, str) and usn.strip():
            return usn.strip().lower().split("::", 1)[0]
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

    def _default_name_for_device(self, device: Device) -> str:
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
        txt_fields = metadata.get("txt") if isinstance(metadata.get("txt"), dict) else {}
        ip = str(device.ip).strip() or "0.0.0.0"

        for key in ("friendlyName", "displayName"):
            value = xml_fields.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        if device.source == "mdns":
            for key in ("name", "friendlyname", "friendly_name", "device", "model"):
                value = txt_fields.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            hostname = metadata.get("hostname")
            if isinstance(hostname, str) and hostname.strip():
                normalized = hostname.strip().removesuffix(".local.").removesuffix(".local").strip(".")
                if normalized:
                    return normalized.replace("-", " ")
            return f"mDNS Device {ip}"

        if device.source == "ssdp":
            server = str(metadata.get("server", ""))
            st = str(metadata.get("st", ""))
            server_low = server.lower()
            st_low = st.lower()
            if "router" in server_low or "wan" in st_low:
                return f"Router {ip}"
            if "mediaserver" in st_low or "dlna" in server_low:
                return f"Media Server {ip}"
            if "printer" in st_low:
                return f"Printer {ip}"
            return f"SSDP Device {ip}"

        return str(device.name).strip() or "Unknown"

    def _default_location_for_device(self, device: Device) -> str:
        metadata = device.metadata if isinstance(device.metadata, dict) else {}
        xml_fields = metadata.get("xml_fields") if isinstance(metadata.get("xml_fields"), dict) else {}
        txt_fields = metadata.get("txt") if isinstance(metadata.get("txt"), dict) else {}

        # Sonos commonly exposes a room label in SSDP XML fields.
        for value in (xml_fields.get("RoomName"), xml_fields.get("roomName"), metadata.get("RoomName"), metadata.get("roomName")):
            if isinstance(value, str) and value.strip():
                s = value.strip()
                if is_plausible_room_location(s):
                    return s

        def txt_by_key_ci(txt: dict, *wanted_lower: str) -> str:
            index: dict[str, str] = {}
            if isinstance(txt, dict):
                for rk, rv in txt.items():
                    if isinstance(rk, str) and isinstance(rv, str):
                        index[rk.lower()] = rv
            for wl in wanted_lower:
                cand = index.get(wl.lower())
                if isinstance(cand, str) and cand.strip():
                    s = cand.strip()
                    if is_plausible_room_location(s):
                        return s
            return ""

        loc = txt_by_key_ci(
            txt_fields,
            "roomname",
            "room_name",
            "room",
            "location",
            "locationname",
            "location_name",
            "zonename",
            "zone_name",
            "zone",
        )
        if loc:
            return loc
        services = metadata.get("services") if isinstance(metadata.get("services"), list) else []
        for svc in services:
            if not isinstance(svc, dict):
                continue
            st = svc.get("txt") if isinstance(svc.get("txt"), dict) else {}
            loc = txt_by_key_ci(st, "roomname", "room_name", "room", "location", "locationname", "zonename", "zone")
            if loc:
                return loc
        return ""

    def _category_for_type(self, device_type: str) -> str:
        return {
            "router": "Routers & Gateways",
            "mediaserver": "Media Servers",
            "printer": "Printers",
            "networkprinter": "Printers",
            "smartspeaker": "Smart Speakers",
            "smarttv": "Smart TVs",
            "smartdevice": "Smart Devices",
            "camera": "Cameras",
            "homeappliance": "Home Appliances",
            "cnc": "CNC Machines",
            "3dprinter": "3D Printers",
            "nas": "NAS / File Servers",
            "computer": "Computers",
            "esp32": "ESP3D Devices",
            "unknown": "Unknown Devices",
        }.get(device_type, "Unknown Devices")

    def set_device_monitored(self, device_key: str, monitored: bool) -> None:
        device = self._devices.get(device_key)
        if device is None:
            return
        if device.monitored == monitored:
            return
        device.monitored = monitored
        self._monitored_overrides[self._make_override_key_for_device(device)] = monitored
        self._notify()

    def _notify(self) -> None:
        snapshot = self.devices
        self._logger.debug("Publishing %d devices to %d listeners", len(snapshot), len(self._listeners))
        for listener in self._listeners:
            listener(snapshot)

    def _on_protocol_event(self, event_type: str, payload: dict) -> None:
        if event_type != "device":
            self._logger.debug("Ignoring protocol event_type=%s", event_type)
            return

        device = Device(
            name=payload.get("name", "Unknown"),
            ip=payload.get("ip", "0.0.0.0"),
            port=int(payload.get("port", 0)),
            type=payload.get("type", "unknown"),
            category=payload.get("category", "Unknown Devices"),
            source=payload.get("source", "unknown"),
            url=payload.get("url"),
            metadata=payload.get("metadata", {}),
            online=bool(payload.get("online", True)),
            icon=payload.get("icon"),
        )
        self.add_or_update_device(device)

    def _load_demo_devices(self) -> None:
        demo_devices = [
            Device(
                name="ESP3D Printer Node",
                ip="192.168.1.42",
                port=80,
                type="esp32",
                category="ESP3D Devices",
                source="mdns",
                url="http://192.168.1.42:80/",
                metadata={
                    "hostname": "esp3d-printer.local.",
                    "server": "esp3d-printer.local.",
                    "interface": "wlan0",
                    "priority": 0,
                    "weight": 0,
                    "ttl": 120,
                    "services": [
                        {
                            "service": "_esp3d._tcp.local.",
                            "port": 80,
                            "hostname": "esp3d-printer.local.",
                            "server": "esp3d-printer.local.",
                        },
                        {
                            "service": "_arduino._tcp.local.",
                            "port": 81,
                            "hostname": "esp3d-printer.local.",
                            "server": "esp3d-printer.local.",
                        },
                    ],
                    "txt": {
                        "board": "ESP32",
                        "fw": "ESP3D 3.0.2",
                        "path": "/",
                        "auth": "false",
                    },
                },
                online=True,
                icon="esp32.png",
            ),
            Device(
                name="Salon Media Server",
                ip="192.168.1.15",
                port=8200,
                type="mediaserver",
                category="Media Servers",
                source="ssdp",
                url="http://192.168.1.15:8200/",
                metadata={
                    "st": "urn:schemas-upnp-org:device:MediaServer:1",
                    "nt": "urn:schemas-upnp-org:device:MediaServer:1",
                    "usn": "uuid:media-server-01::urn:schemas-upnp-org:device:MediaServer:1",
                    "location": "http://192.168.1.15:8200/rootDesc.xml",
                    "server": "Linux/6.8 UPnP/1.1 ReadyMedia/1.3.2",
                    "cache_control": "max-age=1800",
                    "headers": {
                        "HOST": "239.255.255.250:1900",
                        "CACHE-CONTROL": "max-age=1800",
                        "LOCATION": "http://192.168.1.15:8200/rootDesc.xml",
                        "NT": "urn:schemas-upnp-org:device:MediaServer:1",
                        "NTS": "ssdp:alive",
                        "SERVER": "Linux/6.8 UPnP/1.1 ReadyMedia/1.3.2",
                        "USN": "uuid:media-server-01::urn:schemas-upnp-org:device:MediaServer:1",
                    },
                    "xml_fields": {
                        "friendlyName": "Salon Media Server",
                        "deviceType": "urn:schemas-upnp-org:device:MediaServer:1",
                        "manufacturer": "ReadyMedia",
                        "manufacturerURL": "https://www.readymedia.org/",
                        "modelName": "MiniDLNA",
                        "modelURL": "https://www.readymedia.org/docs/",
                        "serialNumber": "RM-001-ABCD",
                        "UDN": "uuid:media-server-01",
                        "presentationURL": "http://192.168.1.15:8200/",
                        "services_description": "urn:schemas-upnp-org:service:ContentDirectory:1, urn:schemas-upnp-org:service:ConnectionManager:1",
                        "icons_description": "mimetype=image/png;width=48;height=48;url=/icons/icon48.png",
                    },
                    "xml": """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <URLBase>http://192.168.1.15:8200/</URLBase>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>Salon Media Server</friendlyName>
    <manufacturer>ReadyMedia</manufacturer>
    <modelName>MiniDLNA</modelName>
    <UDN>uuid:media-server-01</UDN>
  </device>
</root>""",
                },
                online=True,
                icon="mediaserver.png",
            ),
            Device(
                name="Box Internet",
                ip="192.168.1.1",
                port=80,
                type="router",
                category="Routers & Gateways",
                source="ssdp",
                url="http://192.168.1.1:80/",
                metadata={
                    "st": "urn:schemas-upnp-org:device:WANDevice:1",
                    "nt": "urn:schemas-upnp-org:device:WANDevice:1",
                    "usn": "uuid:router-01::urn:schemas-upnp-org:device:WANDevice:1",
                    "location": "http://192.168.1.1:80/igd.xml",
                    "server": "Linux/5.15 UPnP/1.0 RouterOS/7.10",
                    "cache_control": "max-age=1200",
                    "headers": {
                        "HOST": "239.255.255.250:1900",
                        "CACHE-CONTROL": "max-age=1200",
                        "LOCATION": "http://192.168.1.1:80/igd.xml",
                        "NT": "urn:schemas-upnp-org:device:WANDevice:1",
                        "NTS": "ssdp:alive",
                        "SERVER": "Linux/5.15 UPnP/1.0 RouterOS/7.10",
                        "USN": "uuid:router-01::urn:schemas-upnp-org:device:WANDevice:1",
                    },
                    "xml_fields": {
                        "friendlyName": "Box Internet",
                        "deviceType": "urn:schemas-upnp-org:device:WANDevice:1",
                        "manufacturer": "ISP Vendor",
                        "manufacturerURL": "unavailable",
                        "modelName": "Gateway X1",
                        "modelURL": "unavailable",
                        "serialNumber": "GWX1-7788",
                        "UDN": "uuid:router-01",
                        "presentationURL": "http://192.168.1.1/",
                        "services_description": "urn:schemas-upnp-org:service:WANIPConnection:1",
                        "icons_description": "unavailable",
                    },
                    "xml": """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:WANDevice:1</deviceType>
    <friendlyName>Box Internet</friendlyName>
    <manufacturer>ISP Vendor</manufacturer>
    <modelName>Gateway X1</modelName>
    <UDN>uuid:router-01</UDN>
  </device>
</root>""",
                },
                online=True,
                icon="router.png",
            ),
            Device(
                name="NAS Atelier",
                ip="192.168.1.20",
                port=2049,
                type="nas",
                category="NAS / File Servers",
                source="mdns",
                url=None,
                metadata={
                    "hostname": "nas-atelier.local.",
                    "server": "nas-atelier.local.",
                    "interface": "eth0",
                    "priority": 0,
                    "weight": 0,
                    "ttl": 4500,
                    "services": [
                        {
                            "service": "_nfs._tcp.local.",
                            "port": 2049,
                            "hostname": "nas-atelier.local.",
                            "server": "nas-atelier.local.",
                        },
                        {
                            "service": "_mountd._tcp.local.",
                            "port": 32768,
                            "hostname": "nas-atelier.local.",
                            "server": "nas-atelier.local.",
                        },
                    ],
                    "txt": {
                        "model": "DS224+",
                        "vendor": "Synology",
                        "path": "/volume1",
                    },
                },
                online=False,
                icon="nas.png",
            ),
        ]
        for device in demo_devices:
            self._devices[device.key] = device
        self._notify()
