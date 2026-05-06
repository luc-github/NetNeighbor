#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

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
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi
EOF

cat > "${PKG_ROOT}/DEBIAN/postrm" <<'EOF'
#!/usr/bin/env bash
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi
EOF

chmod 0755 "${PKG_ROOT}/DEBIAN/postinst" "${PKG_ROOT}/DEBIAN/postrm"

# Copy application sources directly from the working tree.
# This ensures local modifications (committed or not) are always included.
for path in app.py main.py i18n.py requirements.txt discovery model ui utils data config assets locale LICENSE README.md USER_DOCUMENTATION.md VERSION; do
  if [[ -e "${PROJECT_ROOT}/${path}" ]]; then
    cp -a "${PROJECT_ROOT}/${path}" "${APP_ROOT}/"
  fi
done

# Remove dev artifacts that must not land in the package.
find "${APP_ROOT}" \( -name "__pycache__" -o -name "*.pyc" -o -name "*.pyo" \) -exec rm -rf {} + 2>/dev/null || true

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
chmod 0755 "${PKG_ROOT}/usr/bin/netneighbor" "${PKG_ROOT}/DEBIAN/postinst" "${PKG_ROOT}/DEBIAN/postrm"

INSTALLED_SIZE_KB="$(du -sk "${PKG_ROOT}/usr" | awk '{print $1}')"
cat > "${PKG_ROOT}/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: net
Priority: optional
Architecture: ${ARCH}
Installed-Size: ${INSTALLED_SIZE_KB}
Depends: python3, python3-gi, python3-gi-cairo, gir1.2-gtk-3.0, python3-zeroconf, samba-common-bin, gir1.2-ayatanaappindicator3-0.1 | gir1.2-appindicator3-0.1
Maintainer: Luc LEBOSSE (luc@esp3d.io)
Description: Linux network neighborhood for LAN device discovery
 NetNeighbor is a GTK desktop application for local network discovery
 using SSDP, mDNS, WS-Discovery and NetBIOS, with list/icon views,
 device details, per-device connection commands, and system tray support.
 NetBIOS names use nmblookup from samba-common-bin (Samba server daemons
 are not required). System tray uses Ayatana or GNOME AppIndicator.
EOF

OUTPUT_DEB="${PROJECT_ROOT}/dist/${PKG_NAME}_${VERSION}_${ARCH}.deb"
mkdir -p "${PROJECT_ROOT}/dist"
dpkg-deb --build "${PKG_ROOT}" "${OUTPUT_DEB}"

echo "Built: ${OUTPUT_DEB}"
