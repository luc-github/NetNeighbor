#!/usr/bin/env bash
# Build NetNeighbor.app with PyInstaller (PySide6, onefile). Entry: app/main.py.
#
# --onefile packs everything into a single self-extracting binary inside the
# .app bundle (~200-300 MB vs ~800 MB for onedir). On first launch after
# install or reboot, PyInstaller extracts to $TMPDIR/_MEI<hash>/ (~3-5 s);
# subsequent launches reuse the cache and start instantly.
#
# Prerequisites (macOS):
#   python3 -m venv .venv && source .venv/bin/activate
#   pip install -r requirements.txt pyinstaller
#
# Usage:
#   bash packaging/macos/build_app.sh
#   bash packaging/macos/build_app.sh 2.0.0
#   bash packaging/macos/build_app.sh 2.0.0 arm64
#   bash packaging/macos/build_app.sh 2.0.0 x86_64

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
  echo "Usage: $0 [version] [arch]" >&2
  exit 1
fi

# arch: arm64 | x86_64 | (empty = native)
ARCH="${2:-}"
if [[ -z "${ARCH}" ]]; then
  ARCH="$(uname -m)"
fi

case "${ARCH}" in
  arm64)   ARCH_SUFFIX="arm64"   ;;
  x86_64)  ARCH_SUFFIX="intel"   ;;
  *)
    echo "Unknown arch: ${ARCH}" >&2
    exit 1
    ;;
esac

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
echo "version=${VERSION} arch=${ARCH} entry=app/${ENTRY##*/}"

"${PY}" -m pip install -q -r "${PROJECT_ROOT}/requirements.txt" pyinstaller

rm -rf "${WORK_DIR}"
mkdir -p "${DIST_DIR}"

cd "${PROJECT_ROOT}"
"${PY}" -m PyInstaller \
  --noconfirm \
  --onefile \
  --windowed \
  --name NetNeighbor \
  --target-arch "${ARCH}" \
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

ZIP_OUT="${DIST_DIR}/NetNeighbor-${VERSION}-macos-${ARCH_SUFFIX}.zip"
rm -f "${ZIP_OUT}"
(
  cd "${DIST_DIR}"
  zip -ry "$(basename "${ZIP_OUT}")" "$(basename "${APP_BUNDLE}")"
)

echo "Built:"
echo "  ${APP_BUNDLE}"
echo "  ${ZIP_OUT}"
echo "Post-port: signing/notarization — see packaging/POST_PORT.md"
