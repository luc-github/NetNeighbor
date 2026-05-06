"""GTK dialog helpers — consistent WM decorations across themes."""

from __future__ import annotations

from gi.repository import Gtk


def prepare_gtk_dialog(window: Gtk.Window) -> None:
    """Ensure the dialog has traditional WM decorations (title bar + action area).

    The global ``gtk-dialogs-use-header`` setting is disabled at application startup
    (see ``app.py``) so all ``Gtk.Dialog`` instances already use the action-area layout
    by the time they are constructed.  This function only reinforces the ``decorated``
    flag as a belt-and-suspenders measure.
    """
    try:
        window.set_decorated(True)
    except Exception:
        pass
