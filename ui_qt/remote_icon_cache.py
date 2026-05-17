# File remote_icon_cache.py for NetNeighbor version 1.0.0
# License: LGPL3
"""In-memory Qt pixmaps for device-provided icons (HTTP fetch + GTK-compatible disk cache)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap

from model.device import Device
from utils.device_bundles import DeviceBundle
from utils.device_remote_icon import (
    iter_remote_icon_disk_keys,
    load_remote_icon_payload_for_device,
    persist_remote_icon_index_entry,
    primary_fetch_pair_for_device,
    save_remote_icon_payload,
    urlopen_remote_icon,
)
from utils.icon_view_prefs import DEVICE_ICON_REFERENCE_PX

_LOG = logging.getLogger("ui_qt.remote_icon")


def _is_svg_payload(data: bytes) -> bool:
    sample = data[:1024].lstrip().lower()
    if not sample:
        return False
    if sample.startswith(b"<?xml") or sample.startswith(b"<svg"):
        return True
    return sample.startswith(b"<") and b"<svg" in sample[:4096]


def _trim_transparent_borders(pix: QPixmap) -> QPixmap | None:
    """Crop empty alpha margins (GTK ``_trim_transparent_borders`` parity)."""
    image = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    if image.isNull():
        return None
    width = image.width()
    height = image.height()
    if width <= 0 or height <= 0:
        return None
    min_x = width
    min_y = height
    max_x = -1
    max_y = -1
    for y in range(height):
        for x in range(width):
            if (image.pixel(x, y) >> 24) & 0xFF > 8:
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    if max_x < min_x or max_y < min_y:
        return None
    crop_w = (max_x - min_x) + 1
    crop_h = (max_y - min_y) + 1
    if crop_w == width and crop_h == height:
        return None
    cropped = image.copy(min_x, min_y, crop_w, crop_h)
    if cropped.isNull():
        return None
    out = QPixmap.fromImage(cropped)
    return out if not out.isNull() else None


def _normalize_device_pixmap(pix: QPixmap, size: int) -> QPixmap | None:
    """Scale visible artwork so the tile's longest side matches ``size`` (height-fit heuristic).

    Device UPnP icons are often 48×48; grid presets may be ``large`` (96px) or ``xlarge``.
    We still upscale smoothly so the device artwork appears instead of falling back to the
    bundled type icon.
    """
    if size <= 0:
        return pix
    source = pix
    width = max(1, source.width())
    height = max(1, source.height())
    if (width > size and height < size) or (height > size and width < size):
        return source
    ratio = size / height
    target_w = max(1, int(width * ratio))
    target_h = max(1, int(height * ratio))
    scaled = source.scaled(
        target_w,
        target_h,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return scaled if not scaled.isNull() else source


def _render_svg_pixmap(data: bytes, target_size: int) -> QPixmap | None:
    try:
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:
        return None
    renderer = QSvgRenderer(data)
    if not renderer.isValid():
        return None
    side = target_size if target_size > 0 else DEVICE_ICON_REFERENCE_PX
    pix = QPixmap(side, side)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    if pix.isNull():
        return None
    return pix


def pixmap_from_icon_bytes(data: bytes, target_size: int, *, min_native_size: int = 0) -> QPixmap | None:
    """SVG at target size; raster trimmed and height-normalized like GTK.

    For raster images, pass ``min_native_size`` to reject images whose native
    resolution is below the threshold (avoids blurry upscaling).
    """
    if not data:
        return None
    if target_size <= 0:
        target_size = DEVICE_ICON_REFERENCE_PX
    if _is_svg_payload(data):
        return _render_svg_pixmap(data, target_size)

    image = QImage.fromData(data)
    if image.isNull():
        return None
    pix = QPixmap.fromImage(image)
    if pix.isNull():
        return None
    if min_native_size > 0 and max(pix.width(), pix.height()) < min_native_size:
        return None
    normalized = _normalize_device_pixmap(pix, target_size)
    return normalized


def qicon_from_icon_bytes(data: bytes, target_size: int) -> QIcon | None:
    """Build a multi-size ``QIcon`` anchored at 48px when possible.

    Raster icons are only used when their native resolution is at least
    ``target_size``.  A 48 px device icon will therefore be shown at medium
    (48 px) but NOT at large (96 px) or xlarge (256 px), where the caller
    falls back to the bundled type icon.  SVG icons are always used because
    they are resolution-independent.
    """
    if target_size <= 0:
        target_size = DEVICE_ICON_REFERENCE_PX

    is_svg = _is_svg_payload(data)
    min_native = 0 if is_svg else target_size
    pix = pixmap_from_icon_bytes(data, target_size, min_native_size=min_native)
    if pix is None or pix.isNull():
        return None

    icon = QIcon()
    icon.addPixmap(pix)
    if target_size != DEVICE_ICON_REFERENCE_PX:
        ref_pix = pixmap_from_icon_bytes(data, DEVICE_ICON_REFERENCE_PX)
        if ref_pix is not None and not ref_pix.isNull():
            icon.addPixmap(ref_pix)
    return icon


class QtRemoteIconCache(QObject):
    """Load SSDP/mDNS device icons from disk cache or background HTTP."""

    icons_ready = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._bytes_by_key: dict[str, bytes] = {}
        self._bytes_by_host: dict[str, bytes] = {}
        self._fetching: set[str] = set()

    def clear(self) -> None:
        self._bytes_by_key.clear()
        self._bytes_by_host.clear()

    def icon_for_bundle_device(
        self,
        bundle: DeviceBundle,
        device: Device,
        size: int,
        *,
        on_fetch_started: Callable[[], None] | None = None,
    ) -> QIcon | None:
        data = self._load_bytes(device, on_fetch_started=on_fetch_started)
        if not data:
            return None
        display = size if size > 0 else DEVICE_ICON_REFERENCE_PX
        return qicon_from_icon_bytes(data, display)

    def bytes_for_device(self, device: Device) -> bytes | None:
        """Return cached icon bytes (memory or disk) without triggering a network fetch."""
        sip = str(device.ip).strip()
        if sip:
            hit = self._bytes_by_host.get(sip)
            if hit:
                return hit
        for key in iter_remote_icon_disk_keys(device):
            cached = self._bytes_by_key.get(key)
            if cached:
                return cached
        return load_remote_icon_payload_for_device(device)

    def prefetch_bundle(self, bundle: DeviceBundle, size: int) -> None:
        for dev in (
            bundle.ssdp_device,
            bundle.wsdd_device,
            bundle.wsd_device,
            bundle.nmb_device,
            bundle.mdns_device,
        ):
            if dev is None:
                continue
            if primary_fetch_pair_for_device(dev) is None:
                continue
            self._load_bytes(dev)

    def _load_bytes(
        self,
        device: Device,
        *,
        on_fetch_started: Callable[[], None] | None = None,
    ) -> bytes | None:
        sip = str(device.ip).strip()
        if sip:
            host_hit = self._bytes_by_host.get(sip)
            if host_hit:
                return host_hit

        for key in iter_remote_icon_disk_keys(device):
            cached = self._bytes_by_key.get(key)
            if cached:
                if sip:
                    self._bytes_by_host[sip] = cached
                return cached

        data = load_remote_icon_payload_for_device(device)
        if data:
            self._register_bytes(device, data, iter_remote_icon_disk_keys(device))
            return data

        pair = primary_fetch_pair_for_device(device)
        if pair is None:
            return None
        fetch_url, cache_key = pair
        if cache_key in self._fetching:
            return None
        self._start_fetch(fetch_url, cache_key, device, on_fetch_started)
        return None

    def _register_bytes(
        self, device: Device, data: bytes, disk_keys: list[str]
    ) -> None:
        sip = str(device.ip).strip()
        if sip:
            self._bytes_by_host[sip] = data
        for key in disk_keys:
            self._bytes_by_key[key] = data
        pair = primary_fetch_pair_for_device(device)
        if pair:
            self._bytes_by_key.setdefault(pair[1], data)

    def _start_fetch(
        self,
        fetch_url: str,
        cache_key: str,
        device: Device,
        on_fetch_started: Callable[[], None] | None,
    ) -> None:
        if cache_key in self._fetching:
            return
        self._fetching.add(cache_key)
        if on_fetch_started is not None:
            on_fetch_started()
        sip = str(device.ip).strip()
        disk_keys = list(iter_remote_icon_disk_keys(device))

        def _worker() -> None:
            payload: bytes | None = None
            try:
                with urlopen_remote_icon(fetch_url) as response:
                    payload = response.read()
                if payload:
                    save_remote_icon_payload(cache_key, payload)
            except Exception as exc:
                _LOG.debug("Remote icon fetch failed url=%s: %s", fetch_url, exc)

            def _done() -> None:
                self._fetching.discard(cache_key)
                if payload:
                    self._register_bytes(device, payload, disk_keys)
                    if sip:
                        persist_remote_icon_index_entry(sip, cache_key)
                    self.icons_ready.emit()

            QTimer.singleShot(0, _done)

        threading.Thread(target=_worker, daemon=True).start()
