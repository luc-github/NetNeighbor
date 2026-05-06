"""GTK dialog helpers — consistent WM decorations across themes."""

from __future__ import annotations

import warnings

from gi.repository import Gtk


def prepare_gtk_dialog(window: Gtk.Window) -> None:
    """Avoid header-bar dialogs when settings request them but no header is built.

    With ``gtk-dialogs-use-header`` enabled (common on GNOME), ``Gtk.Dialog`` can end up
    without a proper title bar on some setups, so users cannot minimize or drag the window.
    Force traditional decorations + action buttons area.

    Note: ``use-header-bar`` is a construct-only property and cannot be changed after the
    widget is constructed — the GLib warning is suppressed here since we attempt this only
    as a best-effort hint.
    """
    # use-header-bar is construct-only on Gtk.Dialog; attempting to set it post-construction
    # generates a GLib warning.  We suppress it since this is a best-effort call.
    import gi
    from gi.repository import GLib

    old_handler = GLib.log_set_handler(
        "GLib-GObject",
        GLib.LogLevelFlags.LEVEL_WARNING,
        lambda *_: None,
        None,
    )
    try:
        window.set_property("use-header-bar", False)
    except (TypeError, ValueError, AttributeError):
        pass
    finally:
        GLib.log_remove_handler("GLib-GObject", old_handler)

    try:
        window.set_decorated(True)
    except Exception:
        pass
