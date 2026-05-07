# File wsdd_client.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Poll the local `wsdd` control socket (``-l``) for WSD discovery cache — often includes Windows computer names.

Requires `wsdd` (christgau/wsdd) running with client/discovery mode (``-D``) and a listen socket, e.g.:

    wsdd -D -l /run/wsdd.sock
    # or TCP: ``wsdd -D -l 3737``  →  set ``listen`` to ``3737`` in ``discovery.json``
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time
from collections.abc import Sequence

from discovery.base import BaseDiscovery

_DEFAULT_INTERVAL_S = 60.0
_DEFAULT_SOCKET_TIMEOUT_S = 4.0
# Same logical service port as WSD / our Python WSD rows — helps merge + UI ports column.
_WSDD_DEVICE_PORT = 3702
# DPWS / Windows host type tag in wsdd list(…)
_COMPUTER_TYPE_HINTS = ("computer", "devprof", "device")


def _ip_preference_key(ip_s: str) -> tuple[int, int]:
    """Lower = more useful for display / dedup (LAN IPv4 first)."""
    try:
        a = ipaddress.ip_address(ip_s)
    except ValueError:
        return (99, 0)
    if isinstance(a, ipaddress.IPv4Address):
        if a.is_private and not a.is_link_local and not a.is_loopback:
            return (0, 0)
        if a.is_loopback:
            return (5, 0)
        return (1, 0)
    if isinstance(a, ipaddress.IPv6Address):
        if a.is_link_local:
            return (3, 0)
        if a.is_private:
            return (2, 0)
        return (1, 0)
    return (99, 0)


def _parse_address_field(raw: str) -> list[str]:
    """Split wsdd `addresses` column into bare IP strings (strip interface suffix ``%iface``)."""
    out: list[str] = []
    for token in (raw or "").split(","):
        t = token.strip()
        if not t:
            continue
        left = t.split("%", 1)[0].strip()
        if left.startswith("[") and "]" in left:
            left = left.split("[", 1)[1].split("]", 1)[0]
        if left:
            out.append(left)
    return out


def _best_ip(candidates: Sequence[str]) -> str:
    if not candidates:
        return ""
    scored: list[tuple[tuple[int, int], str]] = []
    for c in candidates:
        try:
            ipaddress.ip_address(c)
        except ValueError:
            continue
        scored.append((_ip_preference_key(c), c))
    if not scored:
        return ""
    scored.sort(key=lambda t: t[0])
    return scored[0][1]


def _infer_type_and_category(types_csv: str) -> tuple[str, str]:
    t = (types_csv or "").lower()
    for hint in _COMPUTER_TYPE_HINTS:
        if hint in t:
            return "computer", _("Computers")
    if "print" in t or "scan" in t or "fax" in t:
        return "multifunction_printer", _("Printers")
    return "unknown", _("Unknown Devices")


def _parse_list_response(text: str) -> list[tuple[str, str, str, str, str, str]]:
    """Return rows ``(uri, name, belongs, last_seen, addresses, types)`` from ``list`` output."""
    rows: list[tuple[str, str, str, str, str, str]] = []
    for line in (text or "").splitlines():
        s = line.rstrip("\r")
        if s.strip() == ".":
            break
        if not s.strip():
            continue
        parts = s.split("\t")
        if len(parts) < 6:
            continue
        rows.append((parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]))
    return rows


def _open_wsdd_socket(listen: str, timeout: float) -> socket.socket:
    spec = (listen or "").strip()
    if not spec:
        raise ValueError("empty listen spec")
    if spec.isdigit():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(("127.0.0.1", int(spec)))
        return s
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(spec)
    return s


def _wsdd_exchange(listen: str, command: str, timeout: float) -> str:
    with _open_wsdd_socket(listen, timeout) as sock:
        data = (command.rstrip() + "\n").encode("utf-8")
        sock.sendall(data)
        parts: list[bytes] = []
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            parts.append(chunk)
            if b".\n" in chunk or chunk.endswith(b".\r\n") or chunk == b".\n":
                break
        return b"".join(parts).decode("utf-8", errors="replace")


class WsddSocketDiscovery(BaseDiscovery):
    """Best-effort poll of a local `wsdd` control API (``list`` / ``probe``)."""

    def __init__(
        self,
        listen: str | None = None,
        interval_seconds: float | None = None,
        socket_timeout_seconds: float | None = None,
        probe_each_poll: bool = True,
    ) -> None:
        super().__init__(source="wsdd")
        self._listen = (listen or "").strip()
        self._interval_s = float(interval_seconds) if interval_seconds is not None else _DEFAULT_INTERVAL_S
        self._timeout_s = float(socket_timeout_seconds) if socket_timeout_seconds is not None else _DEFAULT_SOCKET_TIMEOUT_S
        self._probe_each = bool(probe_each_poll)
        self._logger = logging.getLogger(__name__)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_warned_missing = 0.0

    def start(self) -> None:
        if not self._listen:
            self._logger.info("wsdd socket discovery disabled (no ``listen`` path or port in discovery.json)")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="wsdd-socket", daemon=True)
        self._thread.start()
        self._logger.info("wsdd socket discovery started (listen=%r interval=%.1fs)", self._listen, self._interval_s)

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None:
            t.join(timeout=2.0)
        self._logger.info("wsdd socket discovery stopped")

    def refresh(self) -> None:
        if not self._listen:
            return
        threading.Thread(target=self._poll_once, name="wsdd-refresh", daemon=True).start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._poll_once()
            self._stop.wait(self._interval_s)

    def _poll_once(self) -> None:
        if not self._listen:
            return
        try:
            if self._probe_each:
                try:
                    _wsdd_exchange(self._listen, "probe", min(2.0, self._timeout_s))
                except OSError:
                    self._logger.debug("wsdd probe failed (continuing to list)", exc_info=True)
            text = _wsdd_exchange(self._listen, "list", self._timeout_s)
        except OSError as e:
            now = time.monotonic()
            if now - self._last_warned_missing > 120.0:
                self._last_warned_missing = now
                self._logger.warning(
                    "wsdd control socket unavailable (is wsdd running with -D and -l %r?): %s",
                    self._listen,
                    e,
                )
            return
        except Exception:
            self._logger.debug("wsdd list exchange failed", exc_info=True)
            return

        for row in _parse_list_response(text):
            uri, name, belongs, _last_seen, addr_field, types_csv = row
            addrs = _parse_address_field(addr_field)
            ip_s = _best_ip(addrs)
            if not ip_s:
                self._logger.debug("wsdd row skipped (no address): uri=%.40r name=%r", uri, name)
                continue
            dev_type, category = _infer_type_and_category(types_csv)
            display = (name or "").strip() or "WSD host"
            meta = {
                "wsdd_uri": uri.strip(),
                "wsdd_belongs": (belongs or "").strip(),
                "wsdd_types": (types_csv or "").strip(),
                "wsdd_addresses": (addr_field or "").strip(),
            }
            self._emit(
                "device",
                {
                    "name": display,
                    "ip": ip_s,
                    "port": _WSDD_DEVICE_PORT,
                    "type": dev_type,
                    "category": category,
                    "source": "wsdd",
                    "url": None,
                    "metadata": meta,
                    "online": True,
                    "icon": None,
                },
            )
