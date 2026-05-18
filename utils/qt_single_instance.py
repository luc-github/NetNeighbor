# File qt_single_instance.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Cross-platform single-instance for Qt via QLocalServer (Windows, Linux, macOS)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

# Stable pipe name; must match across launches (aligned with GTK app id io.esp3d.netneighbor).
SINGLETON_SERVER_NAME = "NetNeighbor_io_esp3d_netneighbor_qt"


def send_activate_to_primary(server_name: str = SINGLETON_SERVER_NAME) -> bool:
    """Try to connect to an already running instance and send ``ACTIVATE``.

    Returns ``True`` if a primary responded — the caller should exit without starting a second UI.
    Must be called after ``Q(Core)Application`` exists.
    """
    sock = QLocalSocket()
    sock.connectToServer(server_name)
    if not sock.waitForConnected(200):
        return False
    sock.write(b"ACTIVATE")
    sock.flush()
    sock.waitForBytesWritten(2000)
    sock.disconnectFromServer()
    return True


def create_activation_listener(
    parent: QObject,
    *,
    server_name: str = SINGLETON_SERVER_NAME,
    on_activate: Callable[[], None],
) -> QLocalServer | None:
    """Listen for secondary-instance ``ACTIVATE`` messages and call *on_activate* on the GUI thread.

    Returns the server (keep a reference on the application object) or ``None`` if listen failed.
    """
    QLocalServer.removeServer(server_name)
    server = QLocalServer(parent)

    def _on_new_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is None:
            return
        conn.waitForReadyRead(1500)
        raw = bytes(conn.readAll()).decode("utf-8", errors="ignore").strip()
        conn.disconnectFromServer()
        conn.deleteLater()
        if raw == "ACTIVATE":
            on_activate()

    server.newConnection.connect(_on_new_connection)

    if server.listen(server_name):
        return server

    # Stale lock from a crash — remove and retry once.
    QLocalServer.removeServer(server_name)
    if server.listen(server_name):
        return server

    return None
