#!/usr/bin/env bash
# NetNeighbor 2.0 .deb builder — PySide6/Qt, pip-installed deps, app_qt.py entrypoint.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

VERSION="${1:-}"
ARCH="${2:-$(dpkg --print-architecture)}"

if [[ -z "${VERSION}" ]]; then
  _vf="${PROJECT_ROOT}/VERSION"
  if [[ -f "${_vf}" ]]; then
    VERSION="$(sed -n '1p' "${_vf}" | tr -d '\r' | sed -e 's/#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  fi
fi
if [[ -z "${VERSION}" ]]; then
  echo "Missing version: pass as first argument or set the first line of ${_vf:-${PROJECT_ROOT}/VERSION}"
  echo "Usage: $0 [version] [architecture]"
  echo "Example: $0   # uses VERSION file"
  echo "Example: $0 0.6.0"
  exit 1
fi

PKG_NAME="netneighbor"
BUILD_ROOT="${PROJECT_ROOT}/dist/deb-build"
PKG_ROOT="${BUILD_ROOT}/${PKG_NAME}_${VERSION}_${ARCH}"
APP_ROOT="${PKG_ROOT}/usr/share/netneighbor"

rm -rf "${BUILD_ROOT}"
mkdir -p \
  "${PKG_ROOT}/DEBIAN" \
  "${PKG_ROOT}/usr/bin" \
  "${PKG_ROOT}/usr/share/applications" \
  "${APP_ROOT}"

cat > "${PKG_ROOT}/DEBIAN/postinst" <<'EOF'
#!/usr/bin/env bash
set -e
# Older packages shipped a 64px bitmap under 256x256, which breaks hicolor lookups.
rm -f /usr/share/icons/hicolor/256x256/apps/io.esp3d.netneighbor.png || true
# Remove any __pycache__ directories left over from a previous installation.
find /usr/share/netneighbor -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
# Install Python dependencies (PySide6, zeroconf, WSDiscovery) via pip.
# --break-system-packages is required on Python 3.11+ (Ubuntu 24.04 / Mint 22+).
_reqs=/usr/share/netneighbor/requirements-qt.txt
pip3 install --quiet --break-system-packages -r "$_reqs" 2>/dev/null \
  || pip3 install --quiet -r "$_reqs" || true
# Precompile all sources so the app starts without a write to /usr/share at runtime.
python3 -m compileall -q /usr/share/netneighbor 2>/dev/null || true
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi
EOF

cat > "${PKG_ROOT}/DEBIAN/prerm" <<'EOF'
#!/usr/bin/env bash
set -e
# On remove or upgrade: clean __pycache__ dirs created at runtime by Python
# (postinst compileall + first run).  dpkg only removes files it installed;
# these directories were created after installation so they must be cleaned here.
case "$1" in
  remove|upgrade|deconfigure)
    find /usr/share/netneighbor -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    ;;
esac
EOF

cat > "${PKG_ROOT}/DEBIAN/postrm" <<'EOF'
#!/usr/bin/env bash
set -e
case "$1" in
  remove|purge)
    # Final cleanup: remove any __pycache__ that appeared between prerm and now,
    # then remove the install directory if it is now empty.
    find /usr/share/netneighbor -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    rmdir --ignore-fail-on-non-empty /usr/share/netneighbor 2>/dev/null || true
    if command -v update-desktop-database >/dev/null 2>&1; then
      update-desktop-database /usr/share/applications || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
      gtk-update-icon-cache -q /usr/share/icons/hicolor || true
    fi
    ;;
esac
EOF


# Copy application sources directly from the working tree.
# This ensures local modifications (committed or not) are always included.
for path in main.py app_qt.py i18n.py requirements.txt requirements-qt.txt discovery model ui utils data config assets locale LICENSE README.md USER_DOCUMENTATION.md VERSION; do
  if [[ -e "${PROJECT_ROOT}/${path}" ]]; then
    cp -a "${PROJECT_ROOT}/${path}" "${APP_ROOT}/"
  fi
done

# Remove dev artifacts that must not land in the package.
find "${APP_ROOT}" \( -name "__pycache__" -o -name "*.pyc" -o -name "*.pyo" \) -exec rm -rf {} + 2>/dev/null || true
# Remove the assets/icons/hicolor subtree: it contains relative symlinks
# (../../../../svg/...) that are valid in the source tree but broken once
# installed under /usr/share/netneighbor/.  The actual hicolor icons are
# installed explicitly to /usr/share/icons/hicolor/ by the lines below.
rm -rf "${APP_ROOT}/assets/icons/hicolor"

# Trim bundled-freedesktop to only the resolutions needed by the Qt UI (saves ~143 MB).
# Source tree keeps all resolutions for archival; packages ship only 16/32/48/96/256.
_bfd="${APP_ROOT}/assets/icons/bundled-freedesktop"
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
  find "${APP_ROOT}/locale" -name "*.po" | while read -r _po; do
    _mo="${_po%.po}.mo"
    if [[ ! -f "${_mo}" ]] || [[ "${_po}" -nt "${_mo}" ]]; then
      msgfmt "${_po}" -o "${_mo}" || true
    fi
  done
fi

install -m 0755 "${SCRIPT_DIR}/netneighbor" "${PKG_ROOT}/usr/bin/netneighbor"
sed "s/@APP_VERSION@/${VERSION}/g" "${SCRIPT_DIR}/netneighbor.desktop" > "${PKG_ROOT}/usr/share/applications/netneighbor.desktop"
chmod 0644 "${PKG_ROOT}/usr/share/applications/netneighbor.desktop"
# App icon (launcher, window): full-colour SVG.
mkdir -p "${PKG_ROOT}/usr/share/icons/hicolor/scalable/apps"
install -m 0644 "${PROJECT_ROOT}/assets/svg/netneighbor_icon.svg" "${PKG_ROOT}/usr/share/icons/hicolor/scalable/apps/io.esp3d.netneighbor.svg"
# Tray coloured icon: used on light GTK themes (non-symbolic fallback).
install -m 0644 "${PROJECT_ROOT}/assets/svg/netneighbor-tray.svg" "${PKG_ROOT}/usr/share/icons/hicolor/scalable/apps/io.esp3d.netneighbor-tray.svg"
# Tray symbolic (white): used on dark GTK themes; recoloured by AppIndicator/panel.
# hicolor/scalable/status/ is the standard location for symbolic status icons.
_sym_svg="${PROJECT_ROOT}/assets/svg/netneighbor-tray-symbolic.svg"
if [[ -f "${_sym_svg}" ]]; then
  mkdir -p "${PKG_ROOT}/usr/share/icons/hicolor/scalable/status"
  install -m 0644 "${_sym_svg}" "${PKG_ROOT}/usr/share/icons/hicolor/scalable/status/io.esp3d.netneighbor-tray-symbolic.svg"
fi

find "${PKG_ROOT}" -type d -exec chmod 0755 {} \;
find "${PKG_ROOT}" -type f -exec chmod 0644 {} \;
# Restore executable bit on all maintainer scripts and the launcher.
chmod 0755 \
  "${PKG_ROOT}/usr/bin/netneighbor" \
  "${PKG_ROOT}/DEBIAN/postinst" \
  "${PKG_ROOT}/DEBIAN/prerm" \
  "${PKG_ROOT}/DEBIAN/postrm"

INSTALLED_SIZE_KB="$(du -sk "${PKG_ROOT}/usr" | awk '{print $1}')"
cat > "${PKG_ROOT}/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: net
Priority: optional
Architecture: ${ARCH}
Installed-Size: ${INSTALLED_SIZE_KB}
Depends: python3 (>= 3.10), python3-pip, samba-common-bin
Maintainer: Luc LEBOSSE (luc@esp3d.io)
Description: Discover and monitor devices on your local network
 NetNeighbor is a Qt (PySide6) desktop application that automatically discovers
 all devices on your local network, displays them with icons or in a list,
 and lets you monitor, connect to, and manage them. Supports system tray.
 NetBIOS names use nmblookup from samba-common-bin (Samba server daemons
 are not required). Python dependencies (PySide6, zeroconf, WSDiscovery)
 are installed automatically via pip during package installation.
EOF

OUTPUT_DEB="${PROJECT_ROOT}/dist/${PKG_NAME}_${VERSION}_${ARCH}.deb"
mkdir -p "${PROJECT_ROOT}/dist"
dpkg-deb --build "${PKG_ROOT}" "${OUTPUT_DEB}"

echo "Built: ${OUTPUT_DEB}"
