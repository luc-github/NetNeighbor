#!/usr/bin/env bash
# Build NetNeighbor.app with PyInstaller (PySide6). Entry: app.py (fallback app_qt.py).
#
# Prerequisites (macOS):
#   python3 -m venv .venv && source .venv/bin/activate
#   pip install -r requirements-qt.txt pyinstaller
#
# Usage:
#   bash packaging/macos/build_app.sh
#   bash packaging/macos/build_app.sh 2.0.0

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DIST_DIR="${PROJECT_ROOT}/dist"
WORK_DIR="${PROJECT_ROOT}/build/pyinstaller-macos"

VERSION="${1:-}"
if [[ -z "${VERSION}" ]]; then
  _vf="${PROJECT_ROOT}/VERSION"
  if [[ -f "${_vf}" ]]; then
    VERSION="$(sed -n '1p' "${_vf}" | tr -d '\r' | sed -e 's/#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  fi
fi
if [[ -z "${VERSION}" ]]; then
  echo "Usage: $0 [version]" >&2
  exit 1
fi

PY="${NETNEIGHBOR_PYTHON:-}"
if [[ -z "${PY}" && -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
  PY="${PROJECT_ROOT}/.venv/bin/python"
fi
PY="${PY:-python3}"

ENTRY="${PROJECT_ROOT}/app.py"
if [[ ! -f "${ENTRY}" ]]; then
  ENTRY="${PROJECT_ROOT}/app_qt.py"
  echo "Note: app.py not found — using app_qt.py until rename (see packaging/POST_PORT.md)"
fi
if [[ ! -f "${ENTRY}" ]]; then
  echo "Missing app.py (or app_qt.py during transition)" >&2
  exit 1
fi

echo "== NetNeighbor macOS PyInstaller =="
echo "version=${VERSION} entry=${ENTRY##*/}"

"${PY}" -m pip install -q -r "${PROJECT_ROOT}/requirements-qt.txt" pyinstaller

rm -rf "${WORK_DIR}"
mkdir -p "${DIST_DIR}"

# Reuse Windows spec (onedir COLLECT); produce .app via --windowed on macOS.
cd "${PROJECT_ROOT}"
"${PY}" -m PyInstaller \
  --noconfirm \
  --windowed \
  --name NetNeighbor \
  --distpath "${DIST_DIR}" \
  --workpath "${WORK_DIR}" \
  --specpath "${SCRIPT_DIR}" \
  --add-data "assets:assets" \
  --add-data "locale:locale" \
  --add-data "data:data" \
  --add-data "config:config" \
  --add-data "VERSION:." \
  --hidden-import zeroconf \
  --hidden-import WSDiscovery \
  --hidden-import PySide6.QtCore \
  --hidden-import PySide6.QtGui \
  --hidden-import PySide6.QtWidgets \
  "${ENTRY}"

APP_BUNDLE="${DIST_DIR}/NetNeighbor.app"
if [[ ! -d "${APP_BUNDLE}" ]]; then
  echo "Expected bundle not found: ${APP_BUNDLE}" >&2
  exit 1
fi

ZIP_OUT="${DIST_DIR}/NetNeighbor-${VERSION}-macos.zip"
rm -f "${ZIP_OUT}"
(
  cd "${DIST_DIR}"
  zip -r "$(basename "${ZIP_OUT}")" "$(basename "${APP_BUNDLE}")"
)

echo "Built:"
echo "  ${APP_BUNDLE}"
echo "  ${ZIP_OUT}"
echo "Post-port: signing/notarization — see packaging/POST_PORT.md"
