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
        existing = self._devices.get(device.key)
        if existing is not None:
            # Preserve user-follow choice across updates.
            device.monitored = existing.monitored
        self._devices[device.key] = device
        self._notify()

    def set_device_monitored(self, device_key: str, monitored: bool) -> None:
        device = self._devices.get(device_key)
        if device is None:
            return
        if device.monitored == monitored:
            return
        device.monitored = monitored
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
                metadata={
                    "hostname": "esp3d-printer.local.",
                    "server": "esp3d-printer.local.",
                    "interface": "wlan0",
                    "priority": 0,
                    "weight": 0,
                    "ttl": 120,
                    "services": [
                        {
                            "service": "_esp3d._tcp.local.",
                            "port": 80,
                            "hostname": "esp3d-printer.local.",
                            "server": "esp3d-printer.local.",
                        },
                        {
                            "service": "_arduino._tcp.local.",
                            "port": 81,
                            "hostname": "esp3d-printer.local.",
                            "server": "esp3d-printer.local.",
                        },
                    ],
                    "txt": {
                        "board": "ESP32",
                        "fw": "ESP3D 3.0.2",
                        "path": "/",
                        "auth": "false",
                    },
                },
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
                metadata={
                    "st": "urn:schemas-upnp-org:device:MediaServer:1",
                    "nt": "urn:schemas-upnp-org:device:MediaServer:1",
                    "usn": "uuid:media-server-01::urn:schemas-upnp-org:device:MediaServer:1",
                    "location": "http://192.168.1.15:8200/rootDesc.xml",
                    "server": "Linux/6.8 UPnP/1.1 ReadyMedia/1.3.2",
                    "cache_control": "max-age=1800",
                    "headers": {
                        "HOST": "239.255.255.250:1900",
                        "CACHE-CONTROL": "max-age=1800",
                        "LOCATION": "http://192.168.1.15:8200/rootDesc.xml",
                        "NT": "urn:schemas-upnp-org:device:MediaServer:1",
                        "NTS": "ssdp:alive",
                        "SERVER": "Linux/6.8 UPnP/1.1 ReadyMedia/1.3.2",
                        "USN": "uuid:media-server-01::urn:schemas-upnp-org:device:MediaServer:1",
                    },
                    "xml_fields": {
                        "friendlyName": "Salon Media Server",
                        "deviceType": "urn:schemas-upnp-org:device:MediaServer:1",
                        "manufacturer": "ReadyMedia",
                        "manufacturerURL": "https://www.readymedia.org/",
                        "modelName": "MiniDLNA",
                        "modelURL": "https://www.readymedia.org/docs/",
                        "serialNumber": "RM-001-ABCD",
                        "UDN": "uuid:media-server-01",
                        "presentationURL": "http://192.168.1.15:8200/",
                        "services_description": "urn:schemas-upnp-org:service:ContentDirectory:1, urn:schemas-upnp-org:service:ConnectionManager:1",
                        "icons_description": "mimetype=image/png;width=48;height=48;url=/icons/icon48.png",
                    },
                    "xml": """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <URLBase>http://192.168.1.15:8200/</URLBase>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>Salon Media Server</friendlyName>
    <manufacturer>ReadyMedia</manufacturer>
    <modelName>MiniDLNA</modelName>
    <UDN>uuid:media-server-01</UDN>
  </device>
</root>""",
                },
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
                metadata={
                    "st": "urn:schemas-upnp-org:device:WANDevice:1",
                    "nt": "urn:schemas-upnp-org:device:WANDevice:1",
                    "usn": "uuid:router-01::urn:schemas-upnp-org:device:WANDevice:1",
                    "location": "http://192.168.1.1:80/igd.xml",
                    "server": "Linux/5.15 UPnP/1.0 RouterOS/7.10",
                    "cache_control": "max-age=1200",
                    "headers": {
                        "HOST": "239.255.255.250:1900",
                        "CACHE-CONTROL": "max-age=1200",
                        "LOCATION": "http://192.168.1.1:80/igd.xml",
                        "NT": "urn:schemas-upnp-org:device:WANDevice:1",
                        "NTS": "ssdp:alive",
                        "SERVER": "Linux/5.15 UPnP/1.0 RouterOS/7.10",
                        "USN": "uuid:router-01::urn:schemas-upnp-org:device:WANDevice:1",
                    },
                    "xml_fields": {
                        "friendlyName": "Box Internet",
                        "deviceType": "urn:schemas-upnp-org:device:WANDevice:1",
                        "manufacturer": "ISP Vendor",
                        "manufacturerURL": "unavailable",
                        "modelName": "Gateway X1",
                        "modelURL": "unavailable",
                        "serialNumber": "GWX1-7788",
                        "UDN": "uuid:router-01",
                        "presentationURL": "http://192.168.1.1/",
                        "services_description": "urn:schemas-upnp-org:service:WANIPConnection:1",
                        "icons_description": "unavailable",
                    },
                    "xml": """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:WANDevice:1</deviceType>
    <friendlyName>Box Internet</friendlyName>
    <manufacturer>ISP Vendor</manufacturer>
    <modelName>Gateway X1</modelName>
    <UDN>uuid:router-01</UDN>
  </device>
</root>""",
                },
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
                metadata={
                    "hostname": "nas-atelier.local.",
                    "server": "nas-atelier.local.",
                    "interface": "eth0",
                    "priority": 0,
                    "weight": 0,
                    "ttl": 4500,
                    "services": [
                        {
                            "service": "_nfs._tcp.local.",
                            "port": 2049,
                            "hostname": "nas-atelier.local.",
                            "server": "nas-atelier.local.",
                        },
                        {
                            "service": "_mountd._tcp.local.",
                            "port": 32768,
                            "hostname": "nas-atelier.local.",
                            "server": "nas-atelier.local.",
                        },
                    ],
                    "txt": {
                        "model": "DS224+",
                        "vendor": "Synology",
                        "path": "/volume1",
                    },
                },
                online=False,
                icon="nas.png",
            ),
        ]
        for device in demo_devices:
            self._devices[device.key] = device
        self._notify()
