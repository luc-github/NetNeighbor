#!/usr/bin/env bash
# GTK 1.x portable tarball. After port: app.py + copied paths — POST_PORT.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
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

# Copy application sources directly from the working tree so that local
# modifications (committed or not) are always included — same approach as
# build_deb.sh.  Using "git archive HEAD" would silently exclude any change
# not yet committed, making test builds impossible.
for path in app requirements.txt \
            LICENSE README.md VERSION; do
  if [[ -e "${PROJECT_ROOT}/${path}" ]]; then
    cp -a "${PROJECT_ROOT}/${path}" "${ROOT_DIR}/"
  fi
done
cp -a "${PROJECT_ROOT}/docs/USER_DOCUMENTATION.md" "${ROOT_DIR}/"

# Remove dev artifacts that must not land in the tarball.
find "${ROOT_DIR}" \( -name "__pycache__" -o -name "*.pyc" -o -name "*.pyo" \) \
  -exec rm -rf {} + 2>/dev/null || true

# Trim netneighbor icon set to only the resolutions needed by the Qt UI (saves ~143 MB).
# Source tree keeps all resolutions for archival; packages ship only 16/32/48/96/256.
_bfd="${ROOT_DIR}/app/assets/icons/netneighbor"
if [ -d "${_bfd}" ]; then
    for _d in "${_bfd}"/*/; do
        case "$(basename "${_d%/}")" in
            16|32|48|96|256) ;;
            *) rm -rf "${_d}" ;;
        esac
    done
fi

# Compile .po → .mo for any catalog missing or older than its source.
if command -v msgfmt >/dev/null 2>&1; then
  find "${ROOT_DIR}/app/locale" -name "*.po" | while read -r _po; do
    _mo="${_po%.po}.mo"
    if [[ ! -f "${_mo}" ]] || [[ "${_po}" -nt "${_mo}" ]]; then
      msgfmt "${_po}" -o "${_mo}" || true
    fi
  done
fi

cat > "${ROOT_DIR}/run.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/app/main.py" "$@"
EOF
chmod 0755 "${ROOT_DIR}/run.sh"

install -m 0755 "${SCRIPT_DIR}/install-user-desktop.sh" "${ROOT_DIR}/install-desktop.sh"

OUTPUT_TAR="${DIST_DIR}/${ROOT_NAME}.tar.gz"
tar -C "${STAGE_DIR}" -czf "${OUTPUT_TAR}" "${ROOT_NAME}"

echo "Built: ${OUTPUT_TAR}"
