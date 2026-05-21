#!/usr/bin/env bash
# Write SHA256SUMS-<version>.txt for release artifacts under dist/.
#
# Usage:
#   packaging/checksums.sh <version> [artifact-basename ...]
#
# If no basenames are given, includes every matching file already present in dist/.
# Prints the checksum file path on stdout.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DIST_DIR="${PROJECT_ROOT}/dist"

VERSION="${1:-}"
shift || true

if [[ -z "${VERSION}" ]]; then
  _vf="${PROJECT_ROOT}/VERSION"
  if [[ -f "${_vf}" ]]; then
    VERSION="$(sed -n '1p' "${_vf}" | tr -d '\r' | sed -e 's/#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  fi
fi
if [[ -z "${VERSION}" ]]; then
  echo "Usage: $0 <version> [dist-file ...]" >&2
  exit 1
fi

declare -a FILES=()
if [[ $# -gt 0 ]]; then
  FILES=("$@")
else
  shopt -s nullglob
  for pattern in \
    "netneighbor_${VERSION}_"*.deb \
    "netneighbor-${VERSION}.tar.gz" \
    "NetNeighbor-${VERSION}-"*.AppImage \
    "NetNeighbor-${VERSION}-"*.exe \
    "NetNeighbor-${VERSION}-"*.dmg \
    "NetNeighbor-${VERSION}-macos.zip"
  do
    for f in "${DIST_DIR}"/${pattern}; do
      FILES+=("$(basename "${f}")")
    done
  done
  shopt -u nullglob
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "No artifacts to checksum in ${DIST_DIR}" >&2
  exit 1
fi

checksum_file="${DIST_DIR}/SHA256SUMS-${VERSION}.txt"
mkdir -p "${DIST_DIR}"
(
  cd "${DIST_DIR}"
  for base in "${FILES[@]}"; do
    if [[ ! -f "${base}" ]]; then
      echo "Missing artifact: ${DIST_DIR}/${base}" >&2
      exit 1
    fi
  done
  sha256sum "${FILES[@]}"
) > "${checksum_file}"

echo "${checksum_file}"
