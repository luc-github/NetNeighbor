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
