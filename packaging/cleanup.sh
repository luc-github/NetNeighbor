#!/usr/bin/env bash
# File cleanup.sh for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
#
# Full NetNeighbor cleanup: kills the running process, removes the deb
# installation (system files), user shortcuts, user config and cache.
#
# Usage:
#   sudo bash packaging/cleanup.sh          # full cleanup (needs sudo for /usr)
#   bash packaging/cleanup.sh --user-only   # only user-level files (no sudo needed)

set -euo pipefail

USER_ONLY=false
for arg in "$@"; do
  [[ "${arg}" == "--user-only" ]] && USER_ONLY=true
done

_info()  { echo "  [INFO]  $*"; }
_done()  { echo "  [OK]    $*"; }
_skip()  { echo "  [SKIP]  $*"; }
_warn()  { echo "  [WARN]  $*"; }

echo "=== NetNeighbor cleanup ==="
echo ""

# ── 1. Kill running process ──────────────────────────────────────────────────
echo "-- stopping running instance"
if pkill -f "python3.*netneighbor" 2>/dev/null || \
   pkill -f "python3.*/usr/share/netneighbor/main.py" 2>/dev/null; then
  sleep 0.5
  _done "process killed"
else
  _skip "no running instance found"
fi

# ── 2. System-level removal ──────────────────────────────────────────────────
if [[ "${USER_ONLY}" == false ]]; then
  echo ""
  echo "-- removing system installation"

  # Try dpkg first (cleanest removal via package database)
  if dpkg -s netneighbor >/dev/null 2>&1; then
    _info "dpkg package found — running dpkg -r netneighbor"
    dpkg -r netneighbor
    _done "dpkg removal done"
  else
    _skip "not installed as a dpkg package — removing files manually"
  fi

  # Remove any leftover files regardless of dpkg status
  for path in \
    /usr/share/netneighbor \
    /usr/bin/netneighbor \
    /usr/share/applications/netneighbor.desktop \
    /usr/share/icons/hicolor/scalable/apps/io.esp3d.netneighbor.svg \
    /usr/share/icons/hicolor/scalable/apps/io.esp3d.netneighbor-tray.svg \
    /usr/share/icons/hicolor/scalable/status/io.esp3d.netneighbor-tray-symbolic.svg \
    /usr/share/icons/hicolor/256x256/apps/io.esp3d.netneighbor.png
  do
    if [[ -e "${path}" || -L "${path}" ]]; then
      rm -rf "${path}"
      _done "removed ${path}"
    fi
  done

  # Clean __pycache__ that dpkg/prerm might have missed
  if [[ -d /usr/share/netneighbor ]]; then
    find /usr/share/netneighbor -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    rmdir --ignore-fail-on-non-empty /usr/share/netneighbor 2>/dev/null || true
  fi

  # Update icon and desktop caches
  command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
  command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database /usr/share/applications 2>/dev/null || true
  _done "icon/desktop caches updated"
fi

# ── 3. User-level removal ─────────────────────────────────────────────────────
echo ""
echo "-- removing user-level files"

for path in \
  "${HOME}/.local/share/applications/io.esp3d.netneighbor.desktop" \
  "${HOME}/.local/share/applications/netneighbor.desktop" \
  "${HOME}/.local/share/icons/hicolor/64x64/apps/io.esp3d.netneighbor.png" \
  "${HOME}/.local/share/icons/hicolor/128x128/apps/io.esp3d.netneighbor.png" \
  "${HOME}/.local/share/icons/hicolor/256x256/apps/io.esp3d.netneighbor.png"
do
  if [[ -e "${path}" || -L "${path}" ]]; then
    rm -f "${path}"
    _done "removed ${path}"
  fi
done

for dir in \
  "${HOME}/.config/netneighbor" \
  "${HOME}/.cache/netneighbor"
do
  if [[ -d "${dir}" ]]; then
    rm -rf "${dir}"
    _done "removed ${dir}"
  fi
done

# Autostart entry
autostart="${HOME}/.config/autostart/netneighbor.desktop"
if [[ -f "${autostart}" ]]; then
  rm -f "${autostart}"
  _done "removed ${autostart}"
fi

# Update user desktop/icon caches
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
  gtk-update-icon-cache -q "${HOME}/.local/share/icons/hicolor" 2>/dev/null || true
command -v update-desktop-database >/dev/null 2>&1 && \
  update-desktop-database "${HOME}/.local/share/applications" 2>/dev/null || true

echo ""
echo "=== cleanup complete ==="
