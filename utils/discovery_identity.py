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
