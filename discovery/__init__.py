"""Discovery protocol package."""

from .manager import DiscoveryManager, PresenceTransitionHook, PresenceTransitionKind

__all__ = [
    "DiscoveryManager",
    "PresenceTransitionHook",
    "PresenceTransitionKind",
]
