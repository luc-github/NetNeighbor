# File discovery_identity.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Shared identity hints for cross-protocol device matching (bundle keys, merge)."""

from __future__ import annotations

import re

_UUID_IN_TEXT = re.compile(
    r"(?:urn:uuid:|uuid:)?([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})",
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
