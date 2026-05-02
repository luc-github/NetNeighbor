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


_LOCATION_CANONICAL_EQUIV = {
    "office": "bureau",
    "room": "chambre",
    "living room": "salon",
    "kitchen": "cuisine",
    "workshop": "atelier",
    "bathroom": "salle de bain",
    "master bedroom": "chambre principale",
}


def normalize_location_options(options: list[str]) -> list[str]:
    """Drop empty/invalid entries and collapse obvious FR/EN duplicates.

    Keeps insertion order while preferring the latest value for equivalent labels.
    """
    normalized: list[str] = []
    by_key: dict[str, int] = {}
    for raw in options:
        value = str(raw).strip() if isinstance(raw, str) else ""
        if not value or not is_plausible_room_location(value):
            continue
        key = _LOCATION_CANONICAL_EQUIV.get(value.casefold(), value.casefold())
        if key in by_key:
            normalized[by_key[key]] = value
        else:
            by_key[key] = len(normalized)
            normalized.append(value)
    return normalized
