#!/usr/bin/env bash
# File check_versions.sh for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
#
# Scan all .py files under a directory and report their embedded version header.
# Files missing the "Internal version" line are flagged as errors.
#
# Usage:
#   bash packaging/linux/check_versions.sh                       # scan /usr/share/netneighbor
#   bash packaging/linux/check_versions.sh /path/to/source       # scan a local source tree

set -euo pipefail

SCAN_DIR="${1:-/usr/share/netneighbor}"
LOG_FILE="${TMPDIR:-/tmp}/netneighbor_version_check_$(date +%Y%m%d_%H%M%S).log"

OK=0
MISSING=0
TOTAL=0

echo "=== NetNeighbor version check ==="
echo "Scanning : ${SCAN_DIR}"
echo "Log file : ${LOG_FILE}"
echo ""

{
  echo "NetNeighbor version check — $(date)"
  echo "Directory: ${SCAN_DIR}"
  echo "----------------------------------------------"
} > "${LOG_FILE}"

if [[ ! -d "${SCAN_DIR}" ]]; then
  echo "ERROR: directory not found: ${SCAN_DIR}" | tee -a "${LOG_FILE}"
  exit 1
fi

while IFS= read -r -d '' pyfile; do
  TOTAL=$(( TOTAL + 1 ))
  rel="${pyfile#${SCAN_DIR}/}"

  # Search for "Internal version :" in the first 10 lines
  version_line=""
  while IFS= read -r line; do
    if [[ "${line}" == *"Internal version :"* ]]; then
      version_line="${line}"
      break
    fi
  done < <(head -10 "${pyfile}")

  if [[ -n "${version_line}" ]]; then
    OK=$(( OK + 1 ))
    # Extract just the version/date part after the marker
    info="${version_line#*Internal version : }"
    printf "  %-60s %s\n" "${rel}" "${info}" | tee -a "${LOG_FILE}"
  else
    MISSING=$(( MISSING + 1 ))
    printf "  %-60s *** MISSING VERSION HEADER ***\n" "${rel}" | tee -a "${LOG_FILE}"
  fi

done < <(find "${SCAN_DIR}" -name "*.py" \
           -not -path "*/__pycache__/*" \
           -not -path "*/.venv*" \
           -not -path "*/.git/*" \
           -not -path "*/.claude/*" \
           -not -path "*/dist/*" \
           -not -path "*/build/*" \
           -print0 | sort -z)

echo ""
{
  echo "----------------------------------------------"
  echo "Total : ${TOTAL}  OK : ${OK}  Missing : ${MISSING}"
} | tee -a "${LOG_FILE}"

echo "Log saved: ${LOG_FILE}"

if [[ "${MISSING}" -gt 0 ]]; then
  echo ""
  echo "WARNING: ${MISSING} file(s) are missing the version header."
  exit 1
fi
exit 0
