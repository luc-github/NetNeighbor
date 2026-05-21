#!/usr/bin/env bash
# Linux release: .deb + tar.gz + optional AppImage + SHA256SUMS (GTK 1.x payloads until Qt port).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PACKAGING_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${PROJECT_ROOT}/dist"

VERSION="${1:-}"
ARCH="${2:-$(dpkg --print-architecture)}"
if [[ -z "${VERSION}" ]]; then
  vf="${PROJECT_ROOT}/VERSION"
  if [[ -f "${vf}" ]]; then
    VERSION="$(sed -n '1p' "${vf}" | tr -d '\r' | sed -e 's/#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  fi
fi
if [[ -z "${VERSION}" ]]; then
  echo "Missing version: pass as first argument or set VERSION file." >&2
  echo "Usage: $0 [version] [architecture]" >&2
  exit 1
fi

echo "== NetNeighbor release =="
echo "version=${VERSION} arch=${ARCH}"

echo "-- clean previous artifacts"
# Remove old .deb, .tar.gz, .AppImage, checksums and build staging dirs so
# no stale artifacts can accidentally be installed or mixed with the new build.
rm -f  "${DIST_DIR}"/netneighbor_*.deb \
       "${DIST_DIR}"/netneighbor-*.tar.gz \
       "${DIST_DIR}"/NetNeighbor-*.AppImage \
       "${DIST_DIR}"/SHA256SUMS-*.txt
rm -rf "${DIST_DIR}/deb-build" "${DIST_DIR}/tarball-stage" "${DIST_DIR}/appimage-build"
echo "   dist/ cleaned"

echo "-- update file headers"
python3 "${PROJECT_ROOT}/tools/add_headers.py" --apply

echo "-- sanity checks"
python3 -m py_compile \
  "${PROJECT_ROOT}/app/app_qt.py" \
  "${PROJECT_ROOT}/app/ui/main_window.py" \
  "${PROJECT_ROOT}/app/discovery/manager.py" \
  "${PROJECT_ROOT}/app/utils/app_version.py"
bash -n "${SCRIPT_DIR}/build_deb.sh"
bash -n "${SCRIPT_DIR}/build_tarball.sh"
bash -n "${SCRIPT_DIR}/build_appimage.sh"
bash -n "${SCRIPT_DIR}/install-user-desktop.sh"

echo "-- build artifacts"
"${SCRIPT_DIR}/build_deb.sh"      "${VERSION}" "${ARCH}"
"${SCRIPT_DIR}/build_tarball.sh"  "${VERSION}"
APPIMAGE_ARCH="$(uname -m)"
if "${SCRIPT_DIR}/build_appimage.sh" "${VERSION}" "${APPIMAGE_ARCH}"; then
  _appimage_built=1
else
  echo "   WARNING: AppImage build skipped (appimagetool not found — install from https://github.com/AppImage/AppImageKit/releases)"
  _appimage_built=0
fi

DEB_PATH="${DIST_DIR}/netneighbor_${VERSION}_${ARCH}.deb"
TAR_PATH="${DIST_DIR}/netneighbor-${VERSION}.tar.gz"
APPIMAGE_PATH="${DIST_DIR}/NetNeighbor-${VERSION}-${APPIMAGE_ARCH}.AppImage"

if [[ ! -f "${DEB_PATH}" ]]; then
  echo "Missing built deb: ${DEB_PATH}" >&2
  exit 1
fi
if [[ ! -f "${TAR_PATH}" ]]; then
  echo "Missing built tarball: ${TAR_PATH}" >&2
  exit 1
fi
if [[ "${_appimage_built}" -eq 1 && ! -f "${APPIMAGE_PATH}" ]]; then
  echo "Missing built AppImage: ${APPIMAGE_PATH}" >&2
  exit 1
fi

echo "-- verify deb metadata"
deb_version="$(dpkg-deb -f "${DEB_PATH}" Version)"
if [[ "${deb_version}" != "${VERSION}" ]]; then
  echo "Deb Version mismatch: got=${deb_version} expected=${VERSION}" >&2
  exit 1
fi
deb_size="$(dpkg-deb -f "${DEB_PATH}" Installed-Size)"
if [[ -z "${deb_size}" ]]; then
  echo "Deb Installed-Size is empty" >&2
  exit 1
fi

tmp_extract="$(mktemp -d)"
trap 'rm -rf "${tmp_extract}"' EXIT
dpkg-deb -x "${DEB_PATH}" "${tmp_extract}"

runtime_version_file="${tmp_extract}/usr/share/netneighbor/VERSION"
if [[ ! -f "${runtime_version_file}" ]]; then
  echo "Missing runtime VERSION in deb payload" >&2
  exit 1
fi
runtime_version="$(sed -n '1p' "${runtime_version_file}" | tr -d '\r' | sed -e 's/#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
# Strip pre-release suffix (2.0.0-beta1 → 2.0.0): the repo VERSION file holds the
# stable base version; the tag carries the full pre-release designator.
base_version="${VERSION%%-*}"
if [[ "${runtime_version}" != "${base_version}" ]]; then
  echo "Embedded runtime VERSION mismatch: got=${runtime_version} expected=${base_version} (tag=${VERSION})" >&2
  exit 1
fi

# Spot-check: verify the packaged source matches the local working tree.
# Compares a checksum of a key file between the deb payload and the source tree.
_mw_pkg="${tmp_extract}/usr/share/netneighbor/app/ui/main_window.py"
_mw_src="${PROJECT_ROOT}/app/ui/main_window.py"
if [[ -f "${_mw_pkg}" && -f "${_mw_src}" ]]; then
  _sum_pkg="$(md5sum "${_mw_pkg}" | awk '{print $1}')"
  _sum_src="$(md5sum "${_mw_src}" | awk '{print $1}')"
  if [[ "${_sum_pkg}" != "${_sum_src}" ]]; then
    echo "FATAL: packaged ui/main_window.py differs from the local source." >&2
    echo "       The deb does not reflect the current working tree." >&2
    exit 1
  fi
fi

desktop_file="${tmp_extract}/usr/share/applications/netneighbor.desktop"
if [[ ! -f "${desktop_file}" ]]; then
  echo "Missing desktop file in deb payload" >&2
  exit 1
fi
# Version= in a .desktop file is the Desktop Entry spec version (always "1.0"),
# not the app version.  Verify Name= instead to confirm the file is ours.
desktop_name="$(awk -F= '$1=="Name"{print $2; exit}' "${desktop_file}")"
if [[ "${desktop_name}" != "NetNeighbor" ]]; then
  echo ".desktop Name mismatch: got=${desktop_name} expected=NetNeighbor" >&2
  exit 1
fi

echo "-- checksums"
_checksum_files=(
  "netneighbor_${VERSION}_${ARCH}.deb"
  "netneighbor-${VERSION}.tar.gz"
)
if [[ "${_appimage_built}" -eq 1 ]]; then
  _checksum_files+=("NetNeighbor-${VERSION}-${APPIMAGE_ARCH}.AppImage")
fi
checksum_file="$(bash "${SCRIPT_DIR}/checksums.sh" "${VERSION}" "${_checksum_files[@]}")"

echo "Release artifacts ready:"
echo "  ${DEB_PATH}"
echo "  ${TAR_PATH}"
if [[ "${_appimage_built}" -eq 1 ]]; then
  echo "  ${APPIMAGE_PATH}"
fi
echo "  ${checksum_file}"
echo "Verified: Version=${deb_version} Installed-Size=${deb_size} desktop-Name=${desktop_name}"
