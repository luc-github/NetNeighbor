# File device_actions.py for NetNeighbor version 1.0.0
# License: LGPL3
"""Device actions shared by the Qt main window (open, rename, commands)."""

from __future__ import annotations

from gettext import gettext as _

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from utils.connect_launcher import launch_connect_for_uri
from utils.custom_command import build_argv_from_template, spawn_custom_command_detached
from utils.device_bundles import DeviceBundle


def show_command_error(parent: QWidget, message: str) -> None:
    QMessageBox.critical(parent, _("Error"), message)


def bundle_custom_command(bundle: DeviceBundle) -> str:
    for dev in bundle.devices:
        md = dev.metadata if isinstance(dev.metadata, dict) else {}
        # New: look for custom scheme override in device_commands
        cmds = md.get("device_commands")
        if isinstance(cmds, list):
            for c in cmds:
                if (isinstance(c, dict)
                        and c.get("scheme") == "custom"
                        and c.get("mode") == "override"):
                    tmpl = str(c.get("ip", "")).strip()
                    if tmpl:
                        return tmpl
        # Legacy: flat custom_command field
        cmd = md.get("custom_command")
        if isinstance(cmd, str) and cmd.strip():
            return cmd.strip()
    return ""


def bundle_all_custom_commands(bundle: DeviceBundle) -> list[tuple[str, str]]:
    """Return all custom scheme entries as (label, template) pairs (override first)."""
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for dev in bundle.devices:
        md = dev.metadata if isinstance(dev.metadata, dict) else {}
        cmds = md.get("device_commands")
        if isinstance(cmds, list):
            for c in cmds:
                if not isinstance(c, dict) or c.get("scheme") != "custom":
                    continue
                tmpl = str(c.get("ip", "")).strip()
                if not tmpl or tmpl in seen:
                    continue
                seen.add(tmpl)
                label = str(c.get("label", "")).strip() or (
                    _("Custom command") if c.get("mode") == "override"
                    else _("Custom command (additional)")
                )
                result.append((label, tmpl))
        # Legacy flat field (only if no device_commands custom entry yet)
        if not result:
            cmd = md.get("custom_command")
            if isinstance(cmd, str) and cmd.strip() and cmd.strip() not in seen:
                seen.add(cmd.strip())
                result.append((_("Custom command"), cmd.strip()))
    return result


def run_custom_command_for_bundle(
    parent: QWidget,
    bundle: DeviceBundle,
    *,
    global_template: str,
    connect_templates: dict[str, str],
) -> None:
    per_device = bundle_custom_command(bundle)
    tmpl = per_device or (global_template or "").strip()
    if not tmpl:
        return
    d = bundle.primary
    argv = build_argv_from_template(
        tmpl,
        ip=bundle.ip,
        port=int(bundle.port),
        name=bundle.name or "",
        type_=d.type or "",
        category=d.category or "",
        url="",
    )
    if argv is None:
        show_command_error(
            parent, _("Could not parse the custom command (check placeholders).")
        )
        return

    def _on_err(msg: str) -> None:
        show_command_error(
            parent,
            _("Could not start the command: {error}").format(error=msg),
        )

    spawn_custom_command_detached(argv, on_error=_on_err)


def launch_open_uri(
    parent: QWidget,
    bundle: DeviceBundle,
    uri: str,
    *,
    connect_templates: dict[str, str],
    global_custom_command: str,
) -> None:
    d = bundle.primary
    err = launch_connect_for_uri(
        uri,
        connect_templates,
        ip=bundle.ip,
        port=int(bundle.port),
        name=bundle.name or "",
        type_=d.type or "",
        category=d.category or "",
        device_cmd_override=bundle_custom_command(bundle) or global_custom_command,
    )
    if err:
        show_command_error(
            parent,
            _("Could not start the command: {error}").format(error=err),
        )


def prompt_rename_device(parent: QWidget, current_name: str) -> str | None | object:
    """Return new name, ``None`` to clear override, or ``False`` if cancelled."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(_("Rename device"))
    dlg.setModal(True)
    dlg.resize(360, 120)
    layout = QVBoxLayout(dlg)
    layout.addWidget(QLabel(_("Custom name")))
    entry = QLineEdit()
    entry.setText(current_name)
    entry.selectAll()
    layout.addWidget(entry)
    buttons = QDialogButtonBox()
    btn_reset = buttons.addButton(_("Reset"), QDialogButtonBox.ButtonRole.ResetRole)
    buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
    buttons.addButton(QDialogButtonBox.StandardButton.Save)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    _reset_code = 2
    btn_reset.clicked.connect(lambda: dlg.done(_reset_code))
    layout.addWidget(buttons)

    result = dlg.exec()
    if result == QDialog.DialogCode.Rejected:
        return False
    if result == _reset_code:
        return None
    value = entry.text().strip()
    return value if value else None
