# File device_context_menu.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Right-click context menu for devices (parity with GTK ``DeviceList``)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from gettext import gettext as _

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMenu, QWidget

from utils.device_bundles import DeviceBundle


def _default_type_choices() -> list[tuple[str, str | None]]:
    return [
        (_("Auto"), None),
        (_("NAS"), "nas"),
        (_("Computer"), "computer"),
        (_("Router"), "router"),
        (_("Media server"), "mediaserver"),
        (_("Printer"), "printer"),
        (_("Multifunction printer"), "multifunction_printer"),
        (_("Printer (network / IPP)"), "networkprinter"),
        (_("SmartSpeaker"), "smartspeaker"),
        (_("SmartTV"), "smarttv"),
        (_("SmartDevice"), "smartdevice"),
        (_("Camera"), "camera"),
        (_("HomeAppliance"), "homeappliance"),
        (_("CNC"), "cnc"),
        (_("3D printer"), "3dprinter"),
    ]


def show_device_context_menu(
    parent: QWidget,
    global_pos: QPoint,
    bundle: DeviceBundle,
    *,
    connect_targets: Sequence[tuple[str, str]],
    location_options: Sequence[str],
    type_options: Sequence[tuple[str, str]],
    has_custom_command: bool,
    on_open_uri: Callable[[str], None],
    on_custom_command: Callable[[], None],
    on_details: Callable[[], None],
    on_options: Callable[[], None],
    on_monitor: Callable[[bool], None],
    on_rename: Callable[[], None],
    on_location: Callable[[str | None], None],
    on_type: Callable[[str | None], None],
) -> None:
    menu = QMenu(parent)

    if len(connect_targets) == 1:
        label, uri = connect_targets[0]
        act = menu.addAction(_("Open ({label})").format(label=label))
        act.triggered.connect(lambda _checked=False, u=uri: on_open_uri(u))
    elif connect_targets:
        sub = menu.addMenu(_("Open"))
        for label, uri in connect_targets:
            act = sub.addAction(label)
            act.triggered.connect(lambda _checked=False, u=uri: on_open_uri(u))

    if has_custom_command:
        act_cmd = menu.addAction(_("Run custom command"))
        act_cmd.triggered.connect(on_custom_command)

    act_details = menu.addAction(_("Details"))
    act_details.triggered.connect(on_details)

    act_options = menu.addAction(_("Options"))
    act_options.triggered.connect(on_options)

    if bundle.monitored:
        act_unfollow = menu.addAction(_("Unfollow"))
        act_unfollow.triggered.connect(lambda: on_monitor(False))
    else:
        act_follow = menu.addAction(_("Monitor"))
        act_follow.triggered.connect(lambda: on_monitor(True))

    act_rename = menu.addAction(_("Rename"))
    act_rename.triggered.connect(on_rename)
    menu.addSeparator()

    current_location: str | None = None
    for device in bundle.devices:
        md = device.metadata if isinstance(device.metadata, dict) else {}
        value = md.get("user_location")
        if isinstance(value, str) and value.strip():
            current_location = value.strip()
            break

    loc_menu = menu.addMenu(_("Location"))
    auto_loc = loc_menu.addAction(_("Auto"))
    auto_loc.setCheckable(True)
    auto_loc.setChecked(current_location is None)
    auto_loc.triggered.connect(lambda: on_location(None))
    for loc in location_options:
        act = loc_menu.addAction(loc)
        act.setCheckable(True)
        act.setChecked(loc == current_location)
        act.triggered.connect(lambda _c=False, v=loc: on_location(v))

    type_menu = menu.addMenu(_("Device type"))
    current_type = (
        bundle.primary.type.strip().lower()
        if isinstance(bundle.primary.type, str)
        else "unknown"
    )
    choices = list(type_options) if type_options else _default_type_choices()
    known = {slug for _lbl, slug in choices if slug is not None}
    for label, type_value in choices:
        act = type_menu.addAction(label)
        act.setCheckable(True)
        if type_value is None:
            act.setChecked(current_type not in known)
        else:
            act.setChecked(current_type == type_value)
        act.triggered.connect(
            lambda _c=False, tv=type_value: on_type(tv)
        )

    menu.exec(global_pos)
