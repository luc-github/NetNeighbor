#!/usr/bin/env bash
# Build NetNeighbor.app with PyInstaller (PySide6, onefile) then package as DMG.
#
# The DMG contains NetNeighbor.app and a symlink to /Applications so the user
# can drag-install with one gesture — the standard macOS installation pattern.
#
# --onefile packs everything into a single self-extracting binary inside the
# .app bundle (~200-300 MB vs ~800 MB for onedir). On first launch after
# install or reboot, PyInstaller extracts to $TMPDIR/_MEI<hash>/ (~3-5 s);
# subsequent launches reuse the cache and start instantly.
#
# Prerequisites (macOS):
#   python3 -m venv .venv && source .venv/bin/activate
#   pip install -r requirements.txt pyinstaller
#   brew install create-dmg   # optional but recommended for the full DMG layout
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

# ---------------------------------------------------------------------------
# Build .icns from logo.png (needed by PyInstaller --icon and for DMG volume)
# sips and iconutil are standard macOS tools — no extra install required.
# ---------------------------------------------------------------------------
LOGO_PNG="${PROJECT_ROOT}/app/assets/icons/logo.png"
ICNS_OUT="${PROJECT_ROOT}/build/netneighbor.icns"
if [[ -f "${LOGO_PNG}" ]]; then
  echo "== Building .icns from logo.png =="
  ICONSET_DIR="${PROJECT_ROOT}/build/netneighbor.iconset"
  rm -rf "${ICONSET_DIR}"
  mkdir -p "${ICONSET_DIR}"
  for size in 16 32 64 128 256 512; do
    sips -z "${size}" "${size}" "${LOGO_PNG}" --out "${ICONSET_DIR}/icon_${size}x${size}.png"    > /dev/null 2>&1
    double=$((size * 2))
    sips -z "${double}" "${double}" "${LOGO_PNG}" --out "${ICONSET_DIR}/icon_${size}x${size}@2x.png" > /dev/null 2>&1
  done
  iconutil -c icns "${ICONSET_DIR}" -o "${ICNS_OUT}"
  echo "  icns: ${ICNS_OUT}"
  ICON_ARG="--icon ${ICNS_OUT}"
else
  echo "WARNING: logo.png not found — app will use default icon" >&2
  ICON_ARG=""
fi

echo "== NetNeighbor macOS PyInstaller =="
echo "version=${VERSION} arch=${ARCH} entry=app/${ENTRY##*/}"

rm -rf "${WORK_DIR}"
mkdir -p "${DIST_DIR}"

cd "${PROJECT_ROOT}"
# shellcheck disable=SC2086
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
  ${ICON_ARG} \
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

# ---------------------------------------------------------------------------
# Package as DMG
# Prefer create-dmg (brew install create-dmg) for the full drag-to-Applications
# window; fall back to a plain hdiutil DMG if not installed.
# ---------------------------------------------------------------------------
DMG_OUT="${DIST_DIR}/NetNeighbor-${VERSION}-macos-${ARCH_SUFFIX}.dmg"
rm -f "${DMG_OUT}"

if command -v create-dmg &> /dev/null; then
  echo "== Building DMG with create-dmg =="
  ICON_VOL_ARG=""
  if [[ -f "${ICNS_OUT}" ]]; then
    ICON_VOL_ARG="--volicon ${ICNS_OUT}"
  fi
  # shellcheck disable=SC2086
  create-dmg \
    --volname "NetNeighbor ${VERSION}" \
    ${ICON_VOL_ARG} \
    --window-pos 200 120 \
    --window-size 560 300 \
    --icon-size 128 \
    --icon "NetNeighbor.app" 160 145 \
    --hide-extension "NetNeighbor.app" \
    --app-drop-link 400 145 \
    "${DMG_OUT}" \
    "${APP_BUNDLE}"
else
  echo "== create-dmg not found — building plain DMG with hdiutil =="
  echo "   Install with: brew install create-dmg"
  STAGING="$(mktemp -d)"
  cp -r "${APP_BUNDLE}" "${STAGING}/"
  ln -s /Applications "${STAGING}/Applications"
  hdiutil create \
    -volname "NetNeighbor ${VERSION}" \
    -srcfolder "${STAGING}" \
    -ov -format UDZO \
    "${DMG_OUT}"
  rm -rf "${STAGING}"
fi

echo "Built:"
echo "  ${APP_BUNDLE}"
echo "  ${DMG_OUT}"
echo "Post-port: signing/notarization — see packaging/POST_PORT.md"
