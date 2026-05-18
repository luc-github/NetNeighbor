# File tcp_probe.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-18
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Non-blocking TCP reachability probe for cached devices."""

from __future__ import annotations

import concurrent.futures
import logging
import socket
import threading
from collections.abc import Callable

_logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S: float = 1.5
_DEFAULT_MAX_WORKERS: int = 8


def probe_tcp(ip: str, port: int, timeout_s: float = _DEFAULT_TIMEOUT_S) -> bool:
    """Return True if a TCP connection to ip:port can be established within timeout_s."""
    if not ip or port <= 0:
        return False
    try:
        with socket.create_connection((ip, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def probe_devices_background(
    targets: list[tuple[str, int]],
    on_result: Callable[[str, int, bool], None],
    *,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_workers: int = _DEFAULT_MAX_WORKERS,
) -> None:
    """Probe all (ip, port) targets concurrently; call on_result(ip, port, reachable) for each.

    Runs entirely in background daemon threads — never blocks the caller.
    """
    if not targets:
        return

    def _worker() -> None:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(max_workers, len(targets)),
            thread_name_prefix="tcp-probe",
        ) as pool:
            futures: dict[concurrent.futures.Future[bool], tuple[str, int]] = {
                pool.submit(probe_tcp, ip, port, timeout_s): (ip, port)
                for ip, port in targets
            }
            for fut in concurrent.futures.as_completed(futures):
                ip, port = futures[fut]
                try:
                    reachable = fut.result()
                except Exception:
                    reachable = False
                try:
                    on_result(ip, port, reachable)
                except Exception:
                    _logger.debug(
                        "tcp-probe on_result error for %s:%s", ip, port, exc_info=True
                    )

    threading.Thread(target=_worker, name="tcp-probe-pool", daemon=True).start()
