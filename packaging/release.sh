#!/usr/bin/env bash
# Compatibility wrapper — prefer packaging/linux/release.sh
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/linux/release.sh" "$@"
