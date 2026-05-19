#!/usr/bin/env bash
# File build_appimage.sh for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
#
# Build a NetNeighbor AppImage (system-Python, lightweight ~5-8 MB). GTK 1.x until Qt port.
#
# Usage:
#   bash packaging/linux/build_appimage.sh                        # version from VERSION file
#   bash packaging/linux/build_appimage.sh 1.0.0                  # explicit version
#   bash packaging/linux/build_appimage.sh 1.0.0 x86_64           # explicit version + arch
#   bash packaging/linux/build_appimage.sh --download-tool        # download appimagetool then build
#
# appimagetool is resolved in this order:
#   1. $APPIMAGETOOL env var  (absolute path)
#   2. appimagetool           (in $PATH)
#   3. tools/appimagetool-<arch>.AppImage  (in project root)
#   4. downloaded automatically when --download-tool is passed
#
# Source: https://github.com/AppImage/appimagetool

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ── Parse arguments ────────────────────────────────────────────────────────
DOWNLOAD_TOOL=0
POSITIONAL=()
for _arg in "$@"; do
  case "${_arg}" in
    --download-tool) DOWNLOAD_TOOL=1 ;;
    *) POSITIONAL+=("${_arg}") ;;
  esac
done
set -- "${POSITIONAL[@]+"${POSITIONAL[@]}"}"

VERSION="${1:-}"
ARCH="${2:-$(uname -m)}"   # x86_64 | aarch64 — uname -m, not dpkg arch

# ── Version ────────────────────────────────────────────────────────────────
if [[ -z "${VERSION}" ]]; then
  _vf="${PROJECT_ROOT}/VERSION"
  if [[ -f "${_vf}" ]]; then
    VERSION="$(sed -n '1p' "${_vf}" | tr -d '\r' | \
               sed -e 's/#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  fi
fi
if [[ -z "${VERSION}" ]]; then
  echo "Missing version: pass as first argument or set the first line of VERSION"
  echo "Usage: $0 [version] [arch]"
  exit 1
fi

# ── Locate appimagetool ────────────────────────────────────────────────────
_APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
_APPIMAGETOOL_LOCAL="${PROJECT_ROOT}/tools/appimagetool-${ARCH}.AppImage"

_tool="${APPIMAGETOOL:-}"
if [[ -z "${_tool}" ]]; then
  if command -v appimagetool >/dev/null 2>&1; then
    _tool="appimagetool"
  elif [[ -x "${_APPIMAGETOOL_LOCAL}" ]]; then
    _tool="${_APPIMAGETOOL_LOCAL}"
  fi
fi

if [[ -z "${_tool}" && "${DOWNLOAD_TOOL}" -eq 1 ]]; then
  echo "Downloading appimagetool from ${_APPIMAGETOOL_URL} ..."
  mkdir -p "${PROJECT_ROOT}/tools"
  if command -v wget >/dev/null 2>&1; then
    wget -q --show-progress -O "${_APPIMAGETOOL_LOCAL}" "${_APPIMAGETOOL_URL}"
  elif command -v curl >/dev/null 2>&1; then
    curl -L --progress-bar -o "${_APPIMAGETOOL_LOCAL}" "${_APPIMAGETOOL_URL}"
  else
    echo "Neither wget nor curl found — cannot auto-download appimagetool." >&2
    exit 1
  fi
  chmod +x "${_APPIMAGETOOL_LOCAL}"
  _tool="${_APPIMAGETOOL_LOCAL}"
  echo "Saved: ${_APPIMAGETOOL_LOCAL}"
fi

if [[ -z "${_tool}" ]]; then
  echo "appimagetool not found."
  echo ""
  echo "Run with --download-tool to download it automatically:"
  echo "  bash packaging/linux/build_appimage.sh --download-tool"
  echo ""
  echo "Or download manually from:"
  echo "  ${_APPIMAGETOOL_URL}"
  echo "and place it at: ${_APPIMAGETOOL_LOCAL}  (chmod +x)"
  exit 1
fi

# ── Paths ──────────────────────────────────────────────────────────────────
APP_NAME="NetNeighbor"
BUILD_ROOT="${PROJECT_ROOT}/dist/appimage-build"
APP_DIR="${BUILD_ROOT}/${APP_NAME}.AppDir"
APP_SHARE="${APP_DIR}/usr/share/netneighbor"

rm -rf "${BUILD_ROOT}"
mkdir -p \
  "${APP_DIR}/usr/share/applications" \
  "${APP_DIR}/usr/share/icons/hicolor/scalable/apps" \
  "${APP_DIR}/usr/share/icons/hicolor/scalable/status" \
  "${APP_SHARE}"

# ── App sources ────────────────────────────────────────────────────────────
for path in main.py app_qt.py i18n.py requirements.txt requirements-qt.txt \
            discovery model ui utils data config assets \
            locale LICENSE README.md USER_DOCUMENTATION.md VERSION; do
  if [[ -e "${PROJECT_ROOT}/${path}" ]]; then
    cp -a "${PROJECT_ROOT}/${path}" "${APP_SHARE}/"
  fi
done

# Remove dev artifacts
find "${APP_SHARE}" \
  \( -name "__pycache__" -o -name "*.pyc" -o -name "*.pyo" \) \
  -exec rm -rf {} + 2>/dev/null || true

# The assets/icons/hicolor subtree contains relative symlinks valid only in
# the source tree.  Icons are installed explicitly below.
rm -rf "${APP_SHARE}/assets/icons/hicolor"

# Trim bundled-freedesktop to only the resolutions needed by the Qt UI (saves ~143 MB).
# Source tree keeps all resolutions for archival; packages ship only 16/32/48/96/256.
_bfd="${APP_SHARE}/assets/icons/bundled-freedesktop"
if [ -d "${_bfd}" ]; then
    for _d in "${_bfd}"/*/; do
        case "$(basename "${_d%/}")" in
            16|32|48|96|256) ;;
            *) rm -rf "${_d}" ;;
        esac
    done
fi

# ── Compile .po → .mo ─────────────────────────────────────────────────────
if command -v msgfmt >/dev/null 2>&1; then
  find "${APP_SHARE}/locale" -name "*.po" | while read -r _po; do
    _mo="${_po%.po}.mo"
    if [[ ! -f "${_mo}" ]] || [[ "${_po}" -nt "${_mo}" ]]; then
      msgfmt "${_po}" -o "${_mo}" || true
    fi
  done
fi

# ── AppRun ─────────────────────────────────────────────────────────────────
# $APPDIR is set by the AppImage runtime to the squashfs mount point.
cat > "${APP_DIR}/AppRun" <<'EOF'
#!/usr/bin/env bash
exec python3 "${APPDIR}/usr/share/netneighbor/main.py" "$@"
EOF
chmod 0755 "${APP_DIR}/AppRun"

# ── Desktop file ───────────────────────────────────────────────────────────
# Root-level desktop file: appimagetool uses it to read the app metadata.
# Exec= must be AppRun (the AppImage entry point).
sed "s/@APP_VERSION@/${VERSION}/g" "${SCRIPT_DIR}/netneighbor.desktop" \
  | sed 's|^Exec=.*|Exec=AppRun|' \
  > "${APP_DIR}/io.esp3d.netneighbor.desktop"
chmod 0644 "${APP_DIR}/io.esp3d.netneighbor.desktop"

# Also place it under usr/share/applications so it registers when the
# AppImage is integrated by appimaged or a launcher tool.
sed "s/@APP_VERSION@/${VERSION}/g" "${SCRIPT_DIR}/netneighbor.desktop" \
  > "${APP_DIR}/usr/share/applications/netneighbor.desktop"
chmod 0644 "${APP_DIR}/usr/share/applications/netneighbor.desktop"

# ── Icons ──────────────────────────────────────────────────────────────────
# Root icon: must match the Icon= value in the desktop file (no extension).
install -m 0644 "${PROJECT_ROOT}/assets/svg/netneighbor_icon.svg" \
  "${APP_DIR}/io.esp3d.netneighbor.svg"

# hicolor tree (used by appimaged / desktop integration)
install -m 0644 "${PROJECT_ROOT}/assets/svg/netneighbor_icon.svg" \
  "${APP_DIR}/usr/share/icons/hicolor/scalable/apps/io.esp3d.netneighbor.svg"
install -m 0644 "${PROJECT_ROOT}/assets/svg/netneighbor-tray.svg" \
  "${APP_DIR}/usr/share/icons/hicolor/scalable/apps/io.esp3d.netneighbor-tray.svg"

_sym_svg="${PROJECT_ROOT}/assets/svg/netneighbor-tray-symbolic.svg"
if [[ -f "${_sym_svg}" ]]; then
  install -m 0644 "${_sym_svg}" \
    "${APP_DIR}/usr/share/icons/hicolor/scalable/status/io.esp3d.netneighbor-tray-symbolic.svg"
fi

# ── Permissions ────────────────────────────────────────────────────────────
find "${APP_DIR}" -type d -exec chmod 0755 {} \;
find "${APP_DIR}" -type f -exec chmod 0644 {} \;
chmod 0755 "${APP_DIR}/AppRun"

# ── Build AppImage ─────────────────────────────────────────────────────────
OUTPUT="${PROJECT_ROOT}/dist/${APP_NAME}-${VERSION}-${ARCH}.AppImage"
mkdir -p "${PROJECT_ROOT}/dist"

ARCH="${ARCH}" "${_tool}" "${APP_DIR}" "${OUTPUT}"
chmod 0755 "${OUTPUT}"

echo "Built: ${OUTPUT}"
