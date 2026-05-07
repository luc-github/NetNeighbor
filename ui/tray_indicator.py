# File tray_indicator.py for NetNeighbor version 1.0.0
# Internal version : 1.0.0 date: 2026-05-07 11:44
# Owner: Luc LEBOSSE all copyrights
# License: LGPL3
"""System tray icon — Ayatana AppIndicator / libappindicator, else Gtk.StatusIcon."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

_LOG = logging.getLogger(__name__)

_ICON_THEME_PREPARED = False


def ensure_netneighbor_icon_theme_path() -> None:
    """Register bundled ``assets/icons/`` as an extra icon theme search path."""
    global _ICON_THEME_PREPARED
    if _ICON_THEME_PREPARED:
        return
    root = Path(__file__).resolve().parent.parent / "assets" / "icons"
    _LOG.info("Tray: icon theme path root=%s exists=%s", root, root.is_dir())
    if root.is_dir():
        Gtk.IconTheme.get_default().append_search_path(str(root))
        _LOG.info("Tray: search paths after append: %s", Gtk.IconTheme.get_default().get_search_path())
    _ICON_THEME_PREPARED = True


def _gtk_theme_is_dark() -> bool:
    """Heuristic: true when the GTK theme is dark (dark panel assumed)."""
    try:
        settings = Gtk.Settings.get_default()
        prefer = settings.get_property("gtk-application-prefer-dark-theme")
        name = (settings.get_property("gtk-theme-name") or "")
        result = bool(prefer) or "dark" in name.lower()
        _LOG.info("Tray: gtk-theme-name=%r prefer-dark=%r → is_dark=%s", name, prefer, result)
        return result
    except Exception:
        _LOG.info("Tray: _gtk_theme_is_dark exception", exc_info=True)
        return True


def resolve_tray_icon_name(preferred_base: str) -> str:
    """Pick a tray icon name that exists in the GTK theme.

    AppIndicator renders icons as-is (no symbolic recolouring by the panel).
    On a dark GTK theme we prefer the white symbolic icon; on a light theme we
    prefer the coloured tray icon which is visible on light backgrounds.
    """
    ensure_netneighbor_icon_theme_path()
    theme = Gtk.IconTheme.get_default()
    base = (preferred_base or "").strip() or "io.esp3d.netneighbor-tray"
    if _gtk_theme_is_dark():
        candidates = (
            "io.esp3d.netneighbor-tray-symbolic",
            "io.esp3d.netneighbor-tray",
            f"{base}-symbolic",
            base,
            "io.esp3d.netneighbor-symbolic",
            "io.esp3d.netneighbor",
            "network-workgroup-symbolic",
            "network-transmit-receive-symbolic",
        )
    else:
        candidates = (
            "io.esp3d.netneighbor-tray",
            "io.esp3d.netneighbor",
            f"{base}",
            "network-workgroup",
            "network-workgroup-symbolic",
            "network-transmit-receive-symbolic",
        )
    for name in candidates:
        try:
            found = theme.has_icon(name)
            _LOG.info("Tray: has_icon(%r) = %s", name, found)
            if found:
                _LOG.info("Tray: resolved icon → %r", name)
                return name
        except Exception:
            _LOG.info("Tray: has_icon(%r) raised exception", name, exc_info=True)
            continue
    _LOG.warning("Tray: no icon found in candidates, returning base=%r", base)
    return base


def _load_indicator_module():
    for name, ver in (("AyatanaAppIndicator", "0.1"), ("AppIndicator3", "0.1")):
        try:
            gi.require_version(name, ver)
            mod = getattr(__import__("gi.repository", globals(), locals(), [name], 0), name)
            return mod
        except (ValueError, AttributeError, ImportError):
            continue
    return None


class TrayIndicator:
    """Wraps AppIndicator or Gtk.StatusIcon; menu Open + optional Minimize + Quit."""

    def __init__(
        self,
        *,
        app_id: str,
        icon_name: str,
        tooltip: str,
        menu_open_label: str,
        menu_quit_label: str,
        on_open: Callable[[], None],
        on_quit: Callable[[], None],
        menu_minimize_label: str | None = None,
        on_minimize_to_tray: Callable[[], None] | None = None,
    ) -> None:
        self._on_open = on_open
        self._on_quit = on_quit
        self._minimize_item: Gtk.MenuItem | None = None
        self._indicator_mod = _load_indicator_module()
        self._app_indicator = None
        self._status_icon: Gtk.StatusIcon | None = None
        resolved_icon = resolve_tray_icon_name(icon_name)

        menu = Gtk.Menu()
        open_item = Gtk.MenuItem.new_with_label(menu_open_label)
        open_item.connect("activate", lambda *_a: self._on_open())
        quit_item = Gtk.MenuItem.new_with_label(menu_quit_label)
        quit_item.connect("activate", lambda *_a: self._on_quit())
        menu.append(open_item)
        if on_minimize_to_tray is not None and isinstance(menu_minimize_label, str) and menu_minimize_label.strip():
            mi = Gtk.MenuItem.new_with_label(menu_minimize_label.strip())
            mi.connect("activate", lambda *_a: on_minimize_to_tray())
            menu.append(mi)
            self._minimize_item = mi
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(quit_item)
        menu.show_all()

        if self._indicator_mod is not None:
            try:
                cat = self._indicator_mod.IndicatorCategory.APPLICATION_STATUS
                ind = self._indicator_mod.Indicator.new(app_id, resolved_icon, cat)
                ind.set_title(tooltip)
                ind.set_menu(menu)
                ind.set_status(self._indicator_mod.IndicatorStatus.ACTIVE)
                self._app_indicator = ind
                _LOG.debug("Tray: using AppIndicator (%s)", self._indicator_mod.__name__)
                return
            except Exception:
                _LOG.debug("AppIndicator init failed, falling back", exc_info=True)

        try:
            si = Gtk.StatusIcon()
            si.set_from_icon_name(resolved_icon)
            si.set_tooltip_text(tooltip)
            si.set_name(app_id)
            si.connect("activate", lambda _i: self._on_open())

            def _popup(_icon, button, ts) -> None:
                menu.popup(None, None, Gtk.StatusIcon.position_menu, _icon, button, ts)

            si.connect("popup-menu", _popup)
            self._status_icon = si
            _LOG.debug("Tray: using Gtk.StatusIcon (deprecated but functional on many desktops)")
        except Exception:
            _LOG.warning("System tray icon could not be created", exc_info=True)

    @property
    def available(self) -> bool:
        return self._app_indicator is not None or self._status_icon is not None

    def set_minimize_sensitive(self, sensitive: bool) -> None:
        """Dim the tray \"minimize\" row when the main window is already hidden."""
        if self._minimize_item is not None:
            self._minimize_item.set_sensitive(bool(sensitive))

    def set_icon_name(self, icon_name: str) -> None:
        resolved = resolve_tray_icon_name(icon_name)
        if self._app_indicator is not None:
            try:
                self._app_indicator.set_icon_full(resolved, resolved)
            except Exception:
                try:
                    self._app_indicator.set_property("icon-name", resolved)
                except Exception:
                    pass
        if self._status_icon is not None:
            self._status_icon.set_from_icon_name(resolved)
