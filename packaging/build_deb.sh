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
STAGE_ROOT="${BUILD_ROOT}/app-stage"

rm -rf "${BUILD_ROOT}"
mkdir -p \
  "${PKG_ROOT}/DEBIAN" \
  "${PKG_ROOT}/usr/bin" \
  "${PKG_ROOT}/usr/share/applications" \
  "${PKG_ROOT}/usr/share/icons/hicolor/64x64/apps" \
  "${PKG_ROOT}/usr/share/icons/hicolor/256x256/apps" \
  "${APP_ROOT}" \
  "${STAGE_ROOT}"

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

git -C "${PROJECT_ROOT}" archive --format=tar HEAD | tar -xf - -C "${STAGE_ROOT}"

for path in app.py main.py i18n.py requirements.txt discovery model ui utils data config assets locale LICENSE README.md USER_DOCUMENTATION.md VERSION; do
  if [[ -e "${STAGE_ROOT}/${path}" ]]; then
    cp -a "${STAGE_ROOT}/${path}" "${APP_ROOT}/"
  fi
done
# Ensure local VERSION is embedded even if not yet committed in git archive.
if [[ -f "${PROJECT_ROOT}/VERSION" ]]; then
  install -m 0644 "${PROJECT_ROOT}/VERSION" "${APP_ROOT}/VERSION"
fi

install -m 0755 "${SCRIPT_DIR}/netneighbor" "${PKG_ROOT}/usr/bin/netneighbor"
sed "s/@APP_VERSION@/${VERSION}/g" "${SCRIPT_DIR}/netneighbor.desktop" > "${PKG_ROOT}/usr/share/applications/netneighbor.desktop"
chmod 0644 "${PKG_ROOT}/usr/share/applications/netneighbor.desktop"
# hicolor dirs must match bitmap dimensions (see assets/icons/).
install -m 0644 "${PROJECT_ROOT}/assets/icons/logo.png" "${PKG_ROOT}/usr/share/icons/hicolor/64x64/apps/io.esp3d.netneighbor.png"
install -m 0644 "${PROJECT_ROOT}/assets/icons/logo-256.png" "${PKG_ROOT}/usr/share/icons/hicolor/256x256/apps/io.esp3d.netneighbor.png"

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
Depends: python3, python3-gi, python3-gi-cairo, gir1.2-gtk-3.0, python3-zeroconf
Maintainer: Luc LEBOSSE (luc@esp3d.io)
Description: Linux network neighborhood for SSDP/mDNS discovery
 NetNeighbor is a GTK desktop application for local network discovery
 using SSDP and mDNS, with list/icon views and device details.
EOF

OUTPUT_DEB="${PROJECT_ROOT}/dist/${PKG_NAME}_${VERSION}_${ARCH}.deb"
mkdir -p "${PROJECT_ROOT}/dist"
dpkg-deb --build "${PKG_ROOT}" "${OUTPUT_DEB}"

echo "Built: ${OUTPUT_DEB}"
