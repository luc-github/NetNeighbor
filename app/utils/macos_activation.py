# File macos_activation.py for NetNeighbor version 2.0.0
# Internal version : 2.0.0 date: 2026-05-24 00:00
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""macOS NSApplication activation-policy helpers (ctypes, no pyobjc dependency).

Regular  → Dock icon visible, app menu visible (normal windowed app).
Accessory → No Dock icon, no app menu (menu-bar / tray-only app).

Call set_policy_regular() before showing the main window and
set_policy_accessory() after hiding it to the system tray.
"""

from __future__ import annotations

import sys

if sys.platform != "darwin":

    def set_policy_regular() -> None:
        pass

    def set_policy_accessory() -> None:
        pass

else:
    import ctypes
    import ctypes.util

    _lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc") or "libobjc.dylib")
    _lib.objc_getClass.restype = ctypes.c_void_p
    _lib.objc_getClass.argtypes = [ctypes.c_char_p]
    _lib.sel_registerName.restype = ctypes.c_void_p
    _lib.sel_registerName.argtypes = [ctypes.c_char_p]

    _POLICY_REGULAR = 0    # NSApplicationActivationPolicyRegular
    _POLICY_ACCESSORY = 1  # NSApplicationActivationPolicyAccessory

    def _shared_app() -> int:
        _lib.objc_msgSend.restype = ctypes.c_void_p
        _lib.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        return _lib.objc_msgSend(
            _lib.objc_getClass(b"NSApplication"),
            _lib.sel_registerName(b"sharedApplication"),
        )

    def _set_policy(policy: int) -> None:
        try:
            _lib.objc_msgSend.restype = ctypes.c_void_p
            _lib.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
            _lib.objc_msgSend(
                _shared_app(),
                _lib.sel_registerName(b"setActivationPolicy:"),
                ctypes.c_long(policy),
            )
        except Exception:
            pass

    def set_policy_regular() -> None:
        """Dock icon + app menu visible (normal windowed app)."""
        _set_policy(_POLICY_REGULAR)

    def set_policy_accessory() -> None:
        """No Dock icon, no app menu (tray/menu-bar app)."""
        _set_policy(_POLICY_ACCESSORY)
