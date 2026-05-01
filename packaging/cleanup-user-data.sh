#!/usr/bin/env bash
# Manual cleanup for per-user NetNeighbor data.
set -euo pipefail

CONFIG_DIR="${HOME}/.config/netneighbor"
CACHE_DIR="${HOME}/.cache/netneighbor"

echo "This will remove:"
echo "  - ${CONFIG_DIR}"
echo "  - ${CACHE_DIR}"
echo
read -r -p "Continue? [y/N] " reply
case "${reply}" in
  y|Y|yes|YES)
    rm -rf "${CONFIG_DIR}" "${CACHE_DIR}"
    echo "Removed user data."
    ;;
  *)
    echo "Aborted."
    ;;
esac
