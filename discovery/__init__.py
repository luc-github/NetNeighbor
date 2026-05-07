# File __init__.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""Discovery protocol package."""

from .manager import DiscoveryManager, PresenceTransitionHook, PresenceTransitionKind

__all__ = [
    "DiscoveryManager",
    "PresenceTransitionHook",
    "PresenceTransitionKind",
]
