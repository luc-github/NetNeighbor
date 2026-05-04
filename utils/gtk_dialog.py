"""GTK dialog helpers — consistent WM decorations across themes."""

from __future__ import annotations

from gi.repository import Gtk


def prepare_gtk_dialog(window: Gtk.Window) -> None:
    """Avoid header-bar dialogs when settings request them but no header is built.

    With ``gtk-dialogs-use-header`` enabled (common on GNOME), ``Gtk.Dialog`` can end up
    without a proper title bar on some setups, so users cannot minimize or drag the window.
    Force traditional decorations + action buttons area.
    """
    try:
        window.set_property("use-header-bar", False)
    except (TypeError, ValueError, AttributeError):
        pass
    try:
        window.set_decorated(True)
    except Exception:
        pass
