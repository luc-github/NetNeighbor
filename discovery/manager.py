"""Orchestrates all protocol providers and keeps a simple device cache."""

from collections.abc import Callable
import os

from discovery.base import BaseDiscovery
from discovery.mdns import MDNSDiscovery
from discovery.ssdp import SSDPDiscovery
from model.device import Device


class DiscoveryManager:
    def __init__(self, demo_mode: bool = True) -> None:
        self._protocols: list[BaseDiscovery] = [SSDPDiscovery(), MDNSDiscovery()]
        self._devices: dict[str, Device] = {}
        self._listeners: list[Callable[[list[Device]], None]] = []
        env_demo = os.getenv("NETNEIGHBOR_DEMO", "").strip().lower()
        self._demo_mode = demo_mode and env_demo not in {"0", "false", "no", "off"}

        for protocol in self._protocols:
            protocol.set_callback(self._on_protocol_event)

    def add_listener(self, callback: Callable[[list[Device]], None]) -> None:
        self._listeners.append(callback)
        callback(self.devices)

    @property
    def devices(self) -> list[Device]:
        return sorted(self._devices.values(), key=lambda device: (device.category, device.name.lower()))

    def start(self) -> None:
        if self._demo_mode:
            self._load_demo_devices()
        for protocol in self._protocols:
            protocol.start()

    def stop(self) -> None:
        for protocol in self._protocols:
            protocol.stop()

    def refresh(self) -> None:
        for protocol in self._protocols:
            protocol.refresh()

    def add_or_update_device(self, device: Device) -> None:
        self._devices[device.key] = device
        self._notify()

    def _notify(self) -> None:
        snapshot = self.devices
        for listener in self._listeners:
            listener(snapshot)

    def _on_protocol_event(self, event_type: str, payload: dict) -> None:
        if event_type != "device":
            return

        device = Device(
            name=payload.get("name", "Unknown"),
            ip=payload.get("ip", "0.0.0.0"),
            port=int(payload.get("port", 0)),
            type=payload.get("type", "unknown"),
            category=payload.get("category", "Unknown Devices"),
            source=payload.get("source", "unknown"),
            url=payload.get("url"),
            metadata=payload.get("metadata", {}),
            online=bool(payload.get("online", True)),
            icon=payload.get("icon"),
        )
        self.add_or_update_device(device)

    def _load_demo_devices(self) -> None:
        demo_devices = [
            Device(
                name="ESP3D Printer Node",
                ip="192.168.1.42",
                port=80,
                type="esp32",
                category="ESP3D Devices",
                source="mdns",
                url="http://192.168.1.42:80/",
                metadata={"service": "_esp3d._tcp"},
                online=True,
                icon="esp32.png",
            ),
            Device(
                name="Salon Media Server",
                ip="192.168.1.15",
                port=8200,
                type="mediaserver",
                category="Media Servers",
                source="ssdp",
                url="http://192.168.1.15:8200/",
                metadata={"deviceType": "urn:schemas-upnp-org:device:MediaServer:1"},
                online=True,
                icon="mediaserver.png",
            ),
            Device(
                name="Box Internet",
                ip="192.168.1.1",
                port=80,
                type="router",
                category="Routers & Gateways",
                source="ssdp",
                url="http://192.168.1.1:80/",
                metadata={"deviceType": "urn:schemas-upnp-org:device:WANDevice:1"},
                online=True,
                icon="router.png",
            ),
            Device(
                name="NAS Atelier",
                ip="192.168.1.20",
                port=2049,
                type="nas",
                category="NAS / File Servers",
                source="mdns",
                url=None,
                metadata={"service": "_nfs._tcp"},
                online=False,
                icon="nas.png",
            ),
        ]
        for device in demo_devices:
            self._devices[device.key] = device
        self._notify()
