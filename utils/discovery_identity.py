# File discovery_identity.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Shared identity hints for cross-protocol device matching (bundle keys, merge)."""

from __future__ import annotations

import ipaddress
import re

_UUID_IN_TEXT = re.compile(
    r"(?:urn:uuid:|uuid:)?([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})",
    re.IGNORECASE,
)
# Sonos advertises ZonePlayer / MediaServer / MediaRenderer as separate USN suffixes on one speaker.
_RINCON_MONITORED_UID = re.compile(
    r"^(uuid:rincon_[0-9a-f]+)(?:_mr|_ms)$",
    re.IGNORECASE,
)


def uuid_urn_if_present(text: str) -> str:
    """Return ``urn:uuid:…`` (lowercase) if a UUID appears in WSD EPR, wsdd URI, USN, etc."""
    if not isinstance(text, str) or not text.strip():
        return ""
    m = _UUID_IN_TEXT.search(text.strip())
    if not m:
        return ""
    return f"urn:uuid:{m.group(1).lower()}"


def upnp_identity_from_usn(usn: str) -> str:
    """Stable UPnP row id for bundle merge: USN prefix before ``::`` only.

    Scanning the **full** USN with :func:`uuid_urn_if_present` wrongly picks embedded schema UUIDs
    (e.g. ``00000000-0000-1000-8000-00805f9b34fb``) that are **identical** on many ZonePlayers,
    collapsing different speakers into one bundle so the UI IP label flips as rows overwrite.
    """
    if not isinstance(usn, str) or not usn.strip():
        return ""
    head = usn.strip().split("::", 1)[0].strip()
    if not head:
        return ""
    u = uuid_urn_if_present(head)
    if u:
        return u
    return head.lower()


def normalize_monitored_uid(uid: str) -> str:
    """One follow/monitor identity per physical Sonos (strip ``_MR`` / ``_MS`` service USNs)."""
    if not isinstance(uid, str) or not uid.strip():
        return ""
    u = uid.strip().lower()
    m = _RINCON_MONITORED_UID.match(u)
    if m:
        return m.group(1)
    return u


def normalize_monitored_name(name: str) -> str:
    """Display-name token for follow prefs (skip IPs and placeholder labels)."""
    if not isinstance(name, str) or not name.strip():
        return ""
    collapsed = " ".join(name.strip().split()).lower()
    if not collapsed or collapsed in {"unknown", "wsd host"}:
        return ""
    try:
        ipaddress.ip_address(collapsed.strip("[]"))
        return ""
    except ValueError:
        pass
    if collapsed.startswith("mdns device "):
        return ""
    return collapsed


def upnp_identity_from_udn(udn: str) -> str:
    """Same rule as :func:`upnp_identity_from_usn` for device ``UDN`` fields (may embed ``::``)."""
    if not isinstance(udn, str) or not udn.strip():
        return ""
    head = udn.strip().split("::", 1)[0].strip()
    if not head:
        return ""
    u = uuid_urn_if_present(head)
    if u:
        return u
    return head.lower()
