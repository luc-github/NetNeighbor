#!/usr/bin/env bash
# Build NetNeighbor.app with PyInstaller (PySide6). Entry: app.py (fallback app_qt.py).
#
# Prerequisites (macOS):
#   python3 -m venv .venv && source .venv/bin/activate
#   pip install -r requirements.txt pyinstaller
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

ENTRY="${PROJECT_ROOT}/app/main.py"
if [[ ! -f "${ENTRY}" ]]; then
  echo "Missing app/main.py" >&2
  exit 1
fi

echo "== NetNeighbor macOS PyInstaller =="
echo "version=${VERSION} entry=app/${ENTRY##*/}"

"${PY}" -m pip install -q -r "${PROJECT_ROOT}/requirements.txt" pyinstaller

rm -rf "${WORK_DIR}"
mkdir -p "${DIST_DIR}"

cd "${PROJECT_ROOT}"
"${PY}" -m PyInstaller \
  --noconfirm \
  --windowed \
  --name NetNeighbor \
  --distpath "${DIST_DIR}" \
  --workpath "${WORK_DIR}" \
  --paths "${PROJECT_ROOT}/app" \
  --add-data "${PROJECT_ROOT}/app/assets:assets" \
  --add-data "${PROJECT_ROOT}/app/locale:locale" \
  --add-data "${PROJECT_ROOT}/app/config:config" \
  --add-data "${PROJECT_ROOT}/VERSION:." \
  --hidden-import zeroconf \
  --hidden-import wsdiscovery \
  --hidden-import wsdiscovery.discovery \
  --hidden-import PySide6.QtCore \
  --hidden-import PySide6.QtGui \
  --hidden-import PySide6.QtWidgets \
  --exclude-module PySide6.QtWebEngine \
  --exclude-module PySide6.QtWebEngineCore \
  --exclude-module PySide6.QtWebEngineWidgets \
  --exclude-module PySide6.Qt3DCore \
  --exclude-module PySide6.Qt3DRender \
  --exclude-module PySide6.Qt3DInput \
  --exclude-module PySide6.Qt3DAnimation \
  --exclude-module PySide6.Qt3DExtras \
  --exclude-module PySide6.QtMultimedia \
  --exclude-module PySide6.QtMultimediaWidgets \
  --exclude-module PySide6.QtBluetooth \
  --exclude-module PySide6.QtPositioning \
  --exclude-module PySide6.QtLocation \
  --exclude-module PySide6.QtQuick \
  --exclude-module PySide6.QtQml \
  --exclude-module PySide6.QtQmlModels \
  --exclude-module PySide6.QtDataVisualization \
  --exclude-module PySide6.QtCharts \
  --exclude-module PySide6.QtPdf \
  --exclude-module PySide6.QtPdfWidgets \
  --exclude-module tkinter \
  --exclude-module unittest \
  --exclude-module lib2to3 \
  "${ENTRY}"

APP_BUNDLE="${DIST_DIR}/NetNeighbor.app"
if [[ ! -d "${APP_BUNDLE}" ]]; then
  echo "Expected bundle not found: ${APP_BUNDLE}" >&2
  exit 1
fi

echo "-- strip debug symbols"
find "${APP_BUNDLE}" \( -name "*.dylib" -o -name "*.so" \) \
  -exec strip -x {} \; 2>/dev/null || true

echo "-- bundle layout (Frameworks/ Resources/ top-level)"
echo "   Frameworks/:"; ls "${APP_BUNDLE}/Contents/Frameworks/" 2>/dev/null || echo "   (empty)"
echo "   Resources/:";  ls "${APP_BUNDLE}/Contents/Resources/"  2>/dev/null || echo "   (empty)"

echo "-- deduplicate dirs present in both Frameworks/ and Resources/"
_dedup_count=0
for fw_entry in "${APP_BUNDLE}/Contents/Frameworks/"*/; do
  [[ -d "${fw_entry}" ]] || continue
  name="$(basename "${fw_entry%/}")"
  res_entry="${APP_BUNDLE}/Contents/Resources/${name}"
  if [[ -d "${res_entry}" && ! -L "${res_entry}" ]]; then
    _fw_size="$(du -sk "${fw_entry}" | awk '{print $1}')"
    _res_size="$(du -sk "${res_entry}" | awk '{print $1}')"
    rm -rf "${res_entry}"
    ln -s "../Frameworks/${name}" "${res_entry}"
    echo "   linked Resources/${name} -> Frameworks/${name} (freed ~${_res_size}KB)"
    _dedup_count=$((_dedup_count + 1))
  fi
done
echo "   deduped ${_dedup_count} director(ies)"

ZIP_OUT="${DIST_DIR}/NetNeighbor-${VERSION}-macos.zip"
rm -f "${ZIP_OUT}"
(
  cd "${DIST_DIR}"
  zip -ry "$(basename "${ZIP_OUT}")" "$(basename "${APP_BUNDLE}")"
)

echo "Built:"
echo "  ${APP_BUNDLE}"
echo "  ${ZIP_OUT}"
echo "Post-port: signing/notarization — see packaging/POST_PORT.md"
