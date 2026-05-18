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
