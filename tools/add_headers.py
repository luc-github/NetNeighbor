# File add_headers.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Add or update the standard file header in every NetNeighbor .py source file.

Usage:
    python3 tools/add_headers.py            # dry-run (shows what would change)
    python3 tools/add_headers.py --apply    # write changes to disk
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

VERSION = "2.0.0"
DATE = "2026-05-19 00:00"

# Directories to scan (relative to project root)
SCAN_DIRS = [".", "discovery", "model", "ui", "utils", "tools"]
# Do NOT recurse into these
EXCLUDE_DIRS = {"__pycache__", ".venv", ".git", ".claude", "dist", "build"}

_MARKER = "Internal version :"


def _header(filename: str) -> str:
    return (
        f"# File {filename} for NetNeighbor version {VERSION}\n"
        f"# {_MARKER} {VERSION} date: {DATE}\n"
        f"# Owner: Luc LEBOSSE all copyrights\n"
        f"# License: LGPL3\n"
    )


def _process(path: Path, apply: bool) -> str:
    """Return 'added', 'updated', 'ok', or 'empty'."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"ERROR: {exc}"

    if not content.strip():
        return "empty"

    new_header = _header(path.name)
    lines = content.splitlines(keepends=True)

    # Check whether the file already starts with our header block (all 4 lines)
    header_lines = new_header.splitlines(keepends=True)

    def _existing_header_end() -> int | None:
        """Return index of first non-header line, or None if no header found."""
        if not lines:
            return None
        # Skip an optional shebang on the very first line
        start = 1 if lines[0].startswith("#!") else 0
        if start >= len(lines):
            return None
        if not lines[start].startswith("# File ") or "for NetNeighbor" not in lines[start]:
            return None
        # Find end of contiguous comment block starting at 'start'
        i = start
        while i < len(lines) and lines[i].startswith("#"):
            i += 1
        return i

    end = _existing_header_end()

    if end is not None:
        # Header block exists — replace it with the new one
        rest = "".join(lines[end:])
        new_content = new_header + rest
        if new_content == content:
            return "ok"
        status = "updated"
    else:
        # No header — prepend it
        new_content = new_header + content
        status = "added"

    if apply:
        path.write_text(new_content, encoding="utf-8")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description="Add/update NetNeighbor file headers.")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    counts = {"added": 0, "updated": 0, "ok": 0, "empty": 0, "error": 0}

    for scan in SCAN_DIRS:
        scan_path = project_root / scan
        if not scan_path.is_dir():
            continue
        for py in sorted(scan_path.rglob("*.py")):
            # Skip excluded directories
            if any(part in EXCLUDE_DIRS for part in py.parts):
                continue
            # For root "." scan, only take direct children (not subdirs handled separately)
            if scan == "." and py.parent != project_root:
                continue
            rel = py.relative_to(project_root)
            status = _process(py, args.apply)
            if status.startswith("ERROR"):
                counts["error"] += 1
                print(f"  ERROR  {rel}: {status}")
            elif status == "ok":
                counts["ok"] += 1
            elif status == "empty":
                counts["empty"] += 1
                print(f"  empty  {rel}")
            else:
                counts[status] += 1
                flag = "" if args.apply else " [dry-run]"
                print(f"  {status:<8}{rel}{flag}")

    print()
    if not args.apply:
        print(f"Dry-run: {counts['added']} to add, {counts['updated']} to update, "
              f"{counts['ok']} already up-to-date, {counts['empty']} empty.")
        print("Run with --apply to write changes.")
    else:
        print(f"Done: {counts['added']} added, {counts['updated']} updated, "
              f"{counts['ok']} already up-to-date, {counts['empty']} empty.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
