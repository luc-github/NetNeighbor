#!/usr/bin/env bash
# Install menu shortcut + themed icon under ~/.local (for tarball installs).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ICON_64="${ROOT}/app/assets/icons/logo.png"
ICON_256="${ROOT}/app/assets/icons/logo-256.png"
DESKTOP_DST="${HOME}/.local/share/applications/io.esp3d.netneighbor.desktop"
ICON_DST_64="${HOME}/.local/share/icons/hicolor/64x64/apps/io.esp3d.netneighbor.png"
ICON_DST_256="${HOME}/.local/share/icons/hicolor/256x256/apps/io.esp3d.netneighbor.png"

if [[ ! -f "${ICON_64}" ]]; then
  echo "Missing ${ICON_64}" >&2
  exit 1
fi
if [[ ! -f "${ICON_256}" ]]; then
  echo "Missing ${ICON_256}" >&2
  exit 1
fi

RUN_SH="${ROOT}/run.sh"
if [[ ! -x "${RUN_SH}" ]]; then
  echo "Missing executable ${RUN_SH}" >&2
  exit 1
fi

APP_VERSION=""
if [[ -f "${ROOT}/VERSION" ]]; then
  APP_VERSION="$(sed -n '1p' "${ROOT}/VERSION" | tr -d '\r' | sed -e 's/#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
fi
if [[ -z "${APP_VERSION}" ]]; then
  APP_VERSION="0.0.0"
fi

mkdir -p \
  "${HOME}/.local/share/applications" \
  "$(dirname "${ICON_DST_64}")" \
  "$(dirname "${ICON_DST_256}")"
install -m 0644 "${ICON_64}" "${ICON_DST_64}"
install -m 0644 "${ICON_256}" "${ICON_DST_256}"

{
  echo "[Desktop Entry]"
  echo "Type=Application"
  echo "Version=1.0"
  echo "Name=NetNeighbor"
  echo "Comment=Discover and monitor devices on your local network"
  echo "Comment[de]=Geräte im lokalen Netzwerk entdecken und überwachen"
  echo "Comment[es]=Descubra y monitoree dispositivos en su red local"
  echo "Comment[fr]=Découvrez et surveillez les appareils de votre réseau local"
  echo "Comment[it]=Scopri e monitora i dispositivi sulla tua rete locale"
  echo "Comment[ja]=ローカルネットワーク上のデバイスを検出して監視します"
  echo "Comment[nl]=Apparaten in uw lokale netwerk ontdekken en bewaken"
  echo "Comment[zh_CN]=发现并监控本地网络上的设备"
  echo "Comment[zh_TW]=探索並監控本地網路上的裝置"
  printf 'Exec=%q\n' "${RUN_SH}"
  echo "Icon=io.esp3d.netneighbor"
  echo "Terminal=false"
  echo "Categories=Network;Monitor;"
  echo "StartupNotify=true"
} > "${DESKTOP_DST}"
chmod 0644 "${DESKTOP_DST}"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${HOME}/.local/share/applications" || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q "${HOME}/.local/share/icons/hicolor" || true
fi

echo "Installed: ${DESKTOP_DST}"
echo "Icons: ${ICON_DST_64} ${ICON_DST_256}"
