#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
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

echo "-- sanity checks"
python3 -m py_compile \
  "${PROJECT_ROOT}/app.py" \
  "${PROJECT_ROOT}/ui/main_window.py" \
  "${PROJECT_ROOT}/discovery/manager.py" \
  "${PROJECT_ROOT}/utils/app_version.py"
bash -n "${SCRIPT_DIR}/build_deb.sh"
bash -n "${SCRIPT_DIR}/build_tarball.sh"
bash -n "${SCRIPT_DIR}/install-user-desktop.sh"

echo "-- build artifacts"
"${SCRIPT_DIR}/build_deb.sh" "${VERSION}" "${ARCH}"
"${SCRIPT_DIR}/build_tarball.sh" "${VERSION}"

DEB_PATH="${DIST_DIR}/netneighbor_${VERSION}_${ARCH}.deb"
TAR_PATH="${DIST_DIR}/netneighbor-${VERSION}.tar.gz"

if [[ ! -f "${DEB_PATH}" ]]; then
  echo "Missing built deb: ${DEB_PATH}" >&2
  exit 1
fi
if [[ ! -f "${TAR_PATH}" ]]; then
  echo "Missing built tarball: ${TAR_PATH}" >&2
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
if [[ "${runtime_version}" != "${VERSION}" ]]; then
  echo "Embedded runtime VERSION mismatch: got=${runtime_version} expected=${VERSION}" >&2
  exit 1
fi

desktop_file="${tmp_extract}/usr/share/applications/netneighbor.desktop"
if [[ ! -f "${desktop_file}" ]]; then
  echo "Missing desktop file in deb payload" >&2
  exit 1
fi
desktop_version="$(awk -F= '$1=="Version"{print $2; exit}' "${desktop_file}")"
if [[ "${desktop_version}" != "${VERSION}" ]]; then
  echo ".desktop Version mismatch: got=${desktop_version} expected=${VERSION}" >&2
  exit 1
fi

echo "-- checksums"
checksum_file="${DIST_DIR}/SHA256SUMS-${VERSION}.txt"
(
  cd "${DIST_DIR}"
  sha256sum "netneighbor_${VERSION}_${ARCH}.deb" "netneighbor-${VERSION}.tar.gz"
) > "${checksum_file}"

echo "Release artifacts ready:"
echo "  ${DEB_PATH}"
echo "  ${TAR_PATH}"
echo "  ${checksum_file}"
echo "Verified: Version=${deb_version} Installed-Size=${deb_size} desktop-Version=${desktop_version}"
