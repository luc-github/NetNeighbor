# File tcp_probe.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-19 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Non-blocking TCP reachability probe for cached devices."""

from __future__ import annotations

import concurrent.futures
import logging
import socket
import subprocess
import sys
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


def ping_host(ip: str, timeout_s: float = 2.0) -> bool:
    """Return True if host responds to ICMP ping (no root required on Linux/Windows)."""
    timeout_ms = str(int(timeout_s * 1000))
    timeout_s_int = str(max(1, int(timeout_s)))
    # On Windows, CREATE_NO_WINDOW prevents a console window from flashing on
    # screen for every ping (the subprocess would otherwise pop a brief cmd box).
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    if sys.platform == "win32":
        cmd = ["ping", "-n", "1", "-w", timeout_ms, ip]
    else:
        cmd = ["ping", "-c", "1", "-W", timeout_s_int, ip]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s + 2,
            creationflags=creationflags,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    # Windows ``ping`` exits 0 even when a router/other host answers with
    # "Destination host unreachable" (ICMP type 3) for an offline LAN target —
    # it counts as Received=1, Lost=0. A genuine reply from the target itself
    # always contains "TTL=", which the unreachable/timeout messages do not.
    if sys.platform == "win32":
        return "TTL=" in (result.stdout or "").upper()
    return True


def ping_ips_background(
    ips: list[str],
    on_result: Callable[[str, bool], None],
    *,
    timeout_s: float = 2.0,
    max_workers: int = 16,
) -> None:
    """Ping all IPs concurrently; call on_result(ip, reachable) for each.

    Runs entirely in background daemon threads — never blocks the caller.
    """
    if not ips:
        return

    def _worker() -> None:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(max_workers, len(ips)),
            thread_name_prefix="ping-probe",
        ) as pool:
            futures: dict[concurrent.futures.Future[bool], str] = {
                pool.submit(ping_host, ip, timeout_s): ip
                for ip in ips
            }
            for fut in concurrent.futures.as_completed(futures):
                ip = futures[fut]
                try:
                    reachable = fut.result()
                except Exception:
                    reachable = False
                try:
                    on_result(ip, reachable)
                except Exception:
                    _logger.debug("ping-probe on_result error for %s", ip, exc_info=True)

    threading.Thread(target=_worker, name="ping-probe-pool", daemon=True).start()


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
