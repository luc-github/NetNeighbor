# File check_missing.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Check translation coverage for specific keys in all .po files."""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOCALES = ["fr", "de", "es", "it", "nl", "ja", "zh_CN", "zh_TW"]
KEYS = ["Manufacturer", "Model", "Manufacturer URL", "Model URL",
        "Serial number", "Unique identifier", "MAC address",
        "Friendly name", "Hostname", "Information", "Last seen",
        "Ports", "IP", "Type", "Location"]

for lang in LOCALES:
    path = ROOT / "locale" / lang / "LC_MESSAGES" / "netneighbor.po"
    text = path.read_text(encoding="utf-8")
    missing = []
    for k in KEYS:
        pattern = r'msgid "' + re.escape(k) + r'"\nmsgstr "([^"]*)"'
        m = re.search(pattern, text)
        if not m or not m.group(1).strip():
            missing.append(k)
    if missing:
        print(f"{lang}: MISSING → {missing}")
    else:
        print(f"{lang}: OK")
