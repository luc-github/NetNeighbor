#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VERSION="${1:-}"

if [[ -z "${VERSION}" ]]; then
  _vf="${PROJECT_ROOT}/VERSION"
  if [[ -f "${_vf}" ]]; then
    VERSION="$(sed -n '1p' "${_vf}" | tr -d '\r' | sed -e 's/#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  fi
fi
if [[ -z "${VERSION}" ]]; then
  echo "Missing version: pass as first argument or set the first line of ${_vf:-${PROJECT_ROOT}/VERSION}"
  echo "Usage: $0 [version]"
  echo "Example: $0   # uses VERSION file"
  echo "Example: $0 0.6.0"
  exit 1
fi

DIST_DIR="${PROJECT_ROOT}/dist"
STAGE_DIR="${DIST_DIR}/tarball-stage"
ROOT_NAME="netneighbor-${VERSION}"
ROOT_DIR="${STAGE_DIR}/${ROOT_NAME}"

rm -rf "${STAGE_DIR}"
mkdir -p "${ROOT_DIR}" "${DIST_DIR}"

git -C "${PROJECT_ROOT}" archive --format=tar HEAD | tar -xf - -C "${ROOT_DIR}"
# Ensure local VERSION is embedded even if not yet committed in git archive.
if [[ -f "${PROJECT_ROOT}/VERSION" ]]; then
  install -m 0644 "${PROJECT_ROOT}/VERSION" "${ROOT_DIR}/VERSION"
fi

cat > "${ROOT_DIR}/run.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/main.py" "$@"
EOF
chmod 0755 "${ROOT_DIR}/run.sh"

install -m 0755 "${SCRIPT_DIR}/install-user-desktop.sh" "${ROOT_DIR}/install-desktop.sh"

OUTPUT_TAR="${DIST_DIR}/${ROOT_NAME}.tar.gz"
tar -C "${STAGE_DIR}" -czf "${OUTPUT_TAR}" "${ROOT_NAME}"

echo "Built: ${OUTPUT_TAR}"
