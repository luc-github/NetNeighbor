# File location_label.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Room / location labels: reject mistaken SSDP LOCATION or UPnP descriptor URLs."""


def is_plausible_room_location(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s:
        return False
    low = s.lower()
    if low.startswith(("http://", "https://")):
        return False
    if "description.xml" in low:
        return False
    if "://" in s:
        return False
    return True


def normalize_location_options(options: list[str]) -> list[str]:
    """Drop empty/invalid entries and case-insensitive duplicates.

    Keeps insertion order while preferring the earliest value, so
    user-configured options are never replaced by later (discovered) ones.
    Translations are deliberately NOT handled: "office" and "bureau" are two
    distinct locations and both stay in the list.
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in options:
        value = str(raw).strip() if isinstance(raw, str) else ""
        if not value or not is_plausible_room_location(value):
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return normalized


def location_options_with_current(options: list[str], current: str | None) -> list[str]:
    """Return *options* plus the device's current location label."""
    result = list(options)
    value = current.strip() if isinstance(current, str) else ""
    if not value or not is_plausible_room_location(value):
        return result
    if any(value.casefold() == opt.casefold() for opt in result):
        return result
    result.append(value)
    return result
