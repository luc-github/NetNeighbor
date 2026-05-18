"""Extract all _() strings from source files and show which are missing from .pot."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SOURCE_DIRS = [
    ROOT / "ui_qt",
    ROOT / "discovery",
    ROOT / "utils",
]

def extract_strings(path: Path) -> set[str]:
    out: set[str] = set()
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            out.add(node.args[0].value)
    return out

def load_pot_msgids(pot_path: Path) -> set[str]:
    msgids: set[str] = set()
    current: list[str] = []
    in_msgid = False
    for line in pot_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("msgid "):
            in_msgid = True
            val = line[6:].strip()
            if val.startswith('"') and val.endswith('"'):
                current = [val[1:-1]]
        elif in_msgid and line.startswith('"') and line.endswith('"'):
            current.append(line[1:-1])
        else:
            if in_msgid and current:
                msgids.add("".join(current))
            in_msgid = False
            current = []
    return msgids

all_strings: set[str] = set()
for d in SOURCE_DIRS:
    if not d.exists():
        continue
    for f in d.rglob("*.py"):
        all_strings.update(extract_strings(f))

pot_path = ROOT / "locale" / "netneighbor.pot"
pot_ids = load_pot_msgids(pot_path)

new_strings = sorted(s for s in all_strings if s and s not in pot_ids)
print(f"Total _() strings in source: {len(all_strings)}")
print(f"Existing .pot msgids: {len(pot_ids)}")
print(f"New strings not in .pot: {len(new_strings)}")
print()
for s in new_strings:
    print(repr(s))
