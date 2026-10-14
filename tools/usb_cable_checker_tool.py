"""USB Cable Checker - which cable is which, and what speed you actually get.

A cable can't be read on its own (the e-marker chip is only visible to the USB-C
power controller), so we read the link it produces instead: plug a device in
through the cable and the USB hub driver reports the negotiated speed and whether
the device *could* have gone faster. A USB 3 device that only comes up at 480 Mbps
is the signature of a USB 2 / charging cable; nothing showing up at all means a
charge-only cable.

Windows: talks to the hub driver directly (the same IOCTLs Microsoft's USBView uses),
no admin rights needed. macOS: best effort via system_profiler.

Run this file directly for a text report: python tools/usb_cable_checker_tool.py
"""
import ctypes
import json
import os
import re
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

import customtkinter as ctk

try:
    from base_tool import BaseTool
    import theme
    from theme import C
except ImportError:  # run directly from tools/
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from base_tool import BaseTool
    import theme
    from theme import C

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

HISTORY_FILE = os.path.join(os.path.dirname(theme.SETTINGS_FILE), "usb_cable_history.json")

# Link speeds, slowest to fastest. SUPER_PLUS is 10 Gbps or more (USB 3.2 Gen 2 / Gen 2x2).
LOW, FULL, HIGH, SUPER, SUPER_PLUS = range(5)
SPEEDS = {
    LOW:        ("1.5 Mbps", "USB 1.0 Low Speed"),
    FULL:       ("12 Mbps", "USB 1.1 Full Speed"),
    HIGH:       ("480 Mbps", "USB 2.0 High Speed"),
    SUPER:      ("5 Gbps", "USB 3.2 Gen 1 SuperSpeed"),
    SUPER_PLUS: ("10 Gbps+", "USB 3.2 Gen 2 SuperSpeed+"),
}
# What a file copy realistically reaches at each link speed (MB/s)
REAL_WORLD = {LOW: "0.1", FULL: "1", HIGH: "35-42", SUPER: "350-450", SUPER_PLUS: "700-1050"}

CONNECTION_STATUS = {
    0: "No device", 1: "Connected", 2: "Failed to enumerate", 3: "General failure",
    4: "Caused an overcurrent", 5: "Not enough power", 6: "Not enough bandwidth",
    7: "Hubs nested too deep", 8: "In legacy hub", 9: "Enumerating", 10: "Resetting",
}


@dataclass
class UsbDevice:
    key: tuple
    name: str
    manufacturer: str = ""
    vid: int = 0
    pid: int = 0
    bcd_usb: int = 0
    speed: int = None            # LOW..SUPER_PLUS, None if unknown
    ss_capable: bool = None      # device supports 5 Gbps+ (None = the hub couldn't tell us)
    ssp_capable: bool = None     # device supports 10 Gbps+
    port_usb3: bool = None       # the port it's plugged into has USB 3 lanes
    type_c: bool = None
    user_port: bool = None       # external connector (False = internal webcam, BT, ...)
    is_hub: bool = False
    status: int = 1
    driver: str = ""             # e.g. "USB Attached SCSI (UAS) Mass Storage Device"
    service: str = ""            # e.g. "UASPStor"
    controller: str = ""
    port: int = 0
    upstream: "UsbDevice" = None  # a hub between us and the PC that's running at USB 2 speed
    usb2_half: bool = False      # the USB 2 side of a USB 3 hub (every USB 3 hub has one - normal)
    speed_text: str = ""         # macOS: raw speed string from system_profiler


# ==========================================================================
# Windows backend
# ==========================================================================
if IS_WINDOWS:
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _setupapi = ctypes.WinDLL("setupapi", use_last_error=True)

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    class SP_DEVINFO_DATA(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", GUID), ("DevInst", wintypes.DWORD),
                    ("Reserved", ctypes.c_size_t)]

    class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID), ("Flags", wintypes.DWORD),
                    ("Reserved", ctypes.c_size_t)]

    def _guid(text):
        a, b, c, d, e = text.strip("{}").split("-")
        tail = bytes.fromhex(d + e)
        return GUID(int(a, 16), int(b, 16), int(c, 16), (ctypes.c_ubyte * 8)(*tail))

    GUID_DEVINTERFACE_USB_HOST_CONTROLLER = _guid("{3ABF6F2D-71C4-462A-8A92-1E6861E6AF27}")

    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                     ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                     ctypes.c_void_p]
    _k32.DeviceIoControl.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _k32.ReadFile.argtypes = _k32.WriteFile.argtypes
    _k32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
    _k32.VirtualAlloc.restype = ctypes.c_void_p
    _k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]

    _setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND,
                                               wintypes.DWORD]
    _setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    _setupapi.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID),
                                                      wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
    _setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(SP_DEVINFO_DATA)]
    _setupapi.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]
    _setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    _setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

    INVALID_HANDLE = ctypes.c_void_p(-1).value
    GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
    FILE_SHARE_READ, FILE_SHARE_WRITE = 1, 2
    OPEN_EXISTING, CREATE_ALWAYS = 3, 2
    DIGCF_PRESENT, DIGCF_ALLCLASSES, DIGCF_DEVICEINTERFACE = 0x2, 0x4, 0x10
    SPDRP_DEVICEDESC, SPDRP_SERVICE, SPDRP_DRIVER, SPDRP_FRIENDLYNAME = 0, 4, 9, 12

    def _usb_ioctl(function):
        return (0x22 << 16) | (function << 2)  # CTL_CODE(FILE_DEVICE_USB, fn, METHOD_BUFFERED, FILE_ANY_ACCESS)

    IOCTL_USB_GET_ROOT_HUB_NAME = _usb_ioctl(258)
    IOCTL_USB_GET_NODE_INFORMATION = _usb_ioctl(258)
    IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION = _usb_ioctl(260)
    IOCTL_USB_GET_NODE_CONNECTION_NAME = _usb_ioctl(261)
    IOCTL_USB_GET_NODE_CONNECTION_DRIVERKEY_NAME = _usb_ioctl(264)
    IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX = _usb_ioctl(274)
    IOCTL_USB_GET_PORT_CONNECTOR_PROPERTIES = _usb_ioctl(278)
    IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2 = _usb_ioctl(279)

    def _open(path, access=GENERIC_WRITE, share=FILE_SHARE_WRITE):
        if not path.startswith("\\\\"):
            path = "\\\\.\\" + path
        handle = _k32.CreateFileW(path, access, share, None, OPEN_EXISTING, 0, None)
        return None if handle in (None, INVALID_HANDLE) else handle

    def _ioctl(handle, code, inbuf=b"", size=None):
        """METHOD_BUFFERED call sharing one buffer for in/out. Returns the bytes written, or None."""
        size = max(size or len(inbuf), len(inbuf), 1)
        buf = ctypes.create_string_buffer(inbuf, size)
        returned = wintypes.DWORD()
        if not _k32.DeviceIoControl(handle, code, buf, len(inbuf), buf, size, ctypes.byref(returned), None):
            return None
        return buf.raw[:returned.value]

    def _wstr(data, offset):
        return data[offset:].decode("utf-16-le", "ignore").split("\0", 1)[0] if data else ""

    def _devices_by_driver_key():
        """Map each present USB device's driver key -> (description, service)."""
        found = {}
        info = _setupapi.SetupDiGetClassDevsW(None, "USB", None, DIGCF_ALLCLASSES | DIGCF_PRESENT)
        if info in (None, INVALID_HANDLE):
            return found
        try:
            index = 0
            data = SP_DEVINFO_DATA(cbSize=ctypes.sizeof(SP_DEVINFO_DATA))
            while _setupapi.SetupDiEnumDeviceInfo(info, index, ctypes.byref(data)):
                index += 1
                key = _registry_prop(info, data, SPDRP_DRIVER)
                if key:
                    desc = _registry_prop(info, data, SPDRP_FRIENDLYNAME) or _registry_prop(info, data, SPDRP_DEVICEDESC)
                    found[key.lower()] = (desc, _registry_prop(info, data, SPDRP_SERVICE))
        finally:
            _setupapi.SetupDiDestroyDeviceInfoList(info)
        return found

    def _registry_prop(info, data, prop):
        buf = ctypes.create_string_buffer(1024)
        if _setupapi.SetupDiGetDeviceRegistryPropertyW(info, ctypes.byref(data), prop, None, buf, 1024, None):
            return _wstr(buf.raw, 0)
        return ""

    def _host_controllers():
        """[(device path, description)] for every USB host controller."""
        controllers = []
        guid = GUID_DEVINTERFACE_USB_HOST_CONTROLLER
        info = _setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
        if info in (None, INVALID_HANDLE):
            return controllers
        try:
            index = 0
            iface = SP_DEVICE_INTERFACE_DATA(cbSize=ctypes.sizeof(SP_DEVICE_INTERFACE_DATA))
            while _setupapi.SetupDiEnumDeviceInterfaces(info, None, ctypes.byref(guid), index, ctypes.byref(iface)):
                index += 1
                detail = ctypes.create_string_buffer(2048)
                struct.pack_into("<I", detail, 0, 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)
                devinfo = SP_DEVINFO_DATA(cbSize=ctypes.sizeof(SP_DEVINFO_DATA))
                if _setupapi.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(iface), detail, 2048, None,
                                                              ctypes.byref(devinfo)):
                    controllers.append((_wstr(detail.raw, 4), _registry_prop(info, devinfo, SPDRP_DEVICEDESC)))
        finally:
            _setupapi.SetupDiDestroyDeviceInfoList(info)
        return controllers

    class WindowsScanner:
        def __init__(self):
            self._strings = {}  # string descriptors, cached so a refresh doesn't keep waking devices

        def scan(self):
            drivers = _devices_by_driver_key()
            devices = []
            for c_index, (path, description) in enumerate(_host_controllers()):
                handle = _open(path)
                if not handle:
                    continue
                try:
                    root = _ioctl(handle, IOCTL_USB_GET_ROOT_HUB_NAME, b"", 1024)
                finally:
                    _k32.CloseHandle(handle)
                if root:
                    self._scan_hub(_wstr(root, 4), description or "USB host controller", (c_index,),
                                   drivers, devices, depth=0)
            link_hubs(devices)
            return devices

        def _scan_hub(self, hub_name, controller, path, drivers, devices, depth):
            if depth > 7:
                return
            handle = _open(hub_name)
            if not handle:
                return
            try:
                node = _ioctl(handle, IOCTL_USB_GET_NODE_INFORMATION, bytes(76), 76)
                ports = node[6] if node and len(node) > 6 else 0
                for port in range(1, ports + 1):
                    device = self._read_port(handle, hub_name, port, controller, path, drivers)
                    if device is None:
                        continue
                    devices.append(device)
                    if device.is_hub and device.status == 1:
                        name = _ioctl(handle, IOCTL_USB_GET_NODE_CONNECTION_NAME,
                                      struct.pack("<IIH", port, 0, 0), 1024)
                        if name:
                            self._scan_hub(_wstr(name, 8), controller, path + (port,), drivers, devices,
                                           depth + 1)
            finally:
                _k32.CloseHandle(handle)

        def _read_port(self, handle, hub_name, port, controller, path, drivers):
            ex = _ioctl(handle, IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX, struct.pack("<I", port), 512)
            if not ex or len(ex) < 35:
                return None
            status = struct.unpack_from("<I", ex, 31)[0]
            if status == 0:
                return None
            bcd_usb, = struct.unpack_from("<H", ex, 6)
            vid, pid, bcd_device = struct.unpack_from("<HHH", ex, 12)
            i_manufacturer, i_product = ex[18], ex[19]
            speed = {0: LOW, 1: FULL, 2: HIGH, 3: SUPER}.get(ex[23])
            is_hub = bool(ex[24])

            # USB 3 capability + what the device is actually running at (Windows 8+, USB 3 hubs only)
            ss_capable = ssp_capable = None
            protocols = None
            v2 = _ioctl(handle, IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2,
                        struct.pack("<IIII", port, 16, 0b111, 0), 16)
            if v2 and len(v2) >= 16:
                protocols, flags = struct.unpack_from("<II", v2, 8)
                ss_capable, ssp_capable = bool(flags & 0b10), bool(flags & 0b1000)
                if flags & 0b100:
                    speed = SUPER_PLUS
                elif flags & 0b1:
                    speed = max(speed or SUPER, SUPER)

            # Physical connector: Type-C? external? does it also have USB 3 lanes?
            type_c = user_port = None
            port_usb3 = True if protocols is not None and protocols & 0b100 else None
            props = _ioctl(handle, IOCTL_USB_GET_PORT_CONNECTOR_PROPERTIES, struct.pack("<IIIHHH", port, 0, 0, 0, 0, 0), 512)
            if props and len(props) >= 16:
                flags, _companion_index, companion_port = struct.unpack_from("<IHH", props, 8)
                user_port, type_c = bool(flags & 0b1), bool(flags & 0b1000)
                if port_usb3 is None and companion_port:
                    port_usb3 = self._companion_is_usb3(handle, hub_name, _wstr(props, 16), companion_port)

            driver, service = "", ""
            key_data = _ioctl(handle, IOCTL_USB_GET_NODE_CONNECTION_DRIVERKEY_NAME, struct.pack("<IIH", port, 0, 0), 1024)
            if key_data:
                driver, service = drivers.get(_wstr(key_data, 8).lower(), ("", ""))

            cache = (hub_name, port, vid, pid, bcd_device)
            product = self._string(handle, port, i_product, cache) if status == 1 else ""
            manufacturer = self._string(handle, port, i_manufacturer, cache) if status == 1 else ""

            return UsbDevice(
                key=path + (port, vid, pid), name=product or driver or "USB device {:04X}:{:04X}".format(vid, pid),
                manufacturer=manufacturer, vid=vid, pid=pid, bcd_usb=bcd_usb, speed=speed,
                ss_capable=ss_capable, ssp_capable=ssp_capable, port_usb3=port_usb3, type_c=type_c,
                user_port=user_port, is_hub=is_hub, status=status, driver=driver, service=service,
                controller=controller, port=port)

        def _companion_is_usb3(self, handle, hub_name, companion_hub, companion_port):
            """A USB 3 connector shows up as two logical ports (USB 2 + USB 3). Check the other half."""
            other = None
            if companion_hub and companion_hub.lower() != hub_name.lower():
                other = _open(re.sub(r"^\\\\[?.]\\|^\\\?\?\\", "", companion_hub))
                if not other:
                    return None
            try:
                v2 = _ioctl(other or handle, IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2,
                            struct.pack("<IIII", companion_port, 16, 0b111, 0), 16)
                return bool(struct.unpack_from("<I", v2, 8)[0] & 0b100) if v2 and len(v2) >= 16 else None
            finally:
                if other:
                    _k32.CloseHandle(other)

        def _string(self, handle, port, index, cache):
            if not index:
                return ""
            key = cache + (index,)
            if key not in self._strings:
                request = struct.pack("<IBBHHH", port, 0x80, 6, (3 << 8) | index, 0x0409, 255)
                data = _ioctl(handle, IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION, request, len(request) + 255)
                text = ""
                if data and len(data) > 14 and data[13] == 3:
                    text = data[14:12 + data[12]].decode("utf-16-le", "ignore").strip("\0 ")
                self._strings[key] = text
            return self._strings[key]


def link_hubs(devices):
    """Work out which hubs are the normal USB 2 half of a USB 3 hub, and which devices sit
    behind a hub that's genuinely stuck at USB 2. Keys are hub path + (port, vid, pid)."""
    hubs = {d.key[:-2]: d for d in devices if d.is_hub}
    for hub in sorted(hubs.values(), key=lambda h: len(h.key)):  # parents first
        if hub.ss_capable and hub.speed is not None and hub.speed <= HIGH:
            parent = hubs.get(hub.key[:-3])
            # big hubs chain several hub chips, so a chip behind a USB 2 half is one too
            hub.usb2_half = (parent is not None and parent.usb2_half and parent.vid == hub.vid) or any(
                other.vid == hub.vid and other.key[:-3] == hub.key[:-3] and (other.speed or 0) >= SUPER
                for other in hubs.values())
    for d in devices:
        path = d.key[:-3]
        while len(path) > 1:
            hub = hubs.get(path)
            if hub is None:
                break
            if hub.speed is not None and hub.speed <= HIGH and not hub.usb2_half:
                d.upstream = hub  # the outermost slow hub wins: it limits everything below it
            path = path[:-1]


# ==========================================================================
# macOS backend (best effort: macOS reports the link speed but not what the device could do)
# ==========================================================================
class MacScanner:
    SPEED_WORDS = {"low_speed": LOW, "full_speed": FULL, "high_speed": HIGH,
                   "super_speed": SUPER, "super_speed_plus": SUPER_PLUS}

    def scan(self):
        devices = []
        for data_type in ("SPUSBHostDataType", "SPUSBDataType"):
            try:
                out = subprocess.run(["system_profiler", data_type, "-json"], capture_output=True,
                                     text=True, timeout=20).stdout
                items = json.loads(out or "{}").get(data_type, [])
            except Exception:
                items = []
            for bus_index, bus in enumerate(items):
                self._walk(bus.get("_items", []), (bus_index,), devices)
            if devices:
                break
        return devices

    def _walk(self, items, path, devices):
        for i, item in enumerate(items):
            key = path + (i,)
            speed, speed_text = self._speed(item)
            name = item.get("_name", "USB device")
            vid = self._hex(item.get("vendor_id") or item.get("USBDeviceKeyVendorID"))
            pid = self._hex(item.get("product_id") or item.get("USBDeviceKeyProductID"))
            devices.append(UsbDevice(
                key=key + (vid, pid), name=name,
                manufacturer=item.get("manufacturer") or item.get("USBDeviceKeyVendorName", ""),
                vid=vid, pid=pid, speed=speed, speed_text=speed_text, is_hub="hub" in name.lower(),
                user_port=True))
            self._walk(item.get("_items", []), key, devices)

    def _speed(self, item):
        for k, v in item.items():
            if "speed" not in k.lower() or not isinstance(v, str):
                continue
            word = v.lower().strip()
            if word in self.SPEED_WORDS:
                return self.SPEED_WORDS[word], v
            m = re.search(r"([\d.]+)\s*(g|m)b", word)
            if m:
                mbps = float(m.group(1)) * (1000 if m.group(2) == "g" else 1)
                speed = SUPER_PLUS if mbps >= 10000 else SUPER if mbps >= 5000 else HIGH if mbps >= 480 \
                    else FULL if mbps >= 12 else LOW
                return speed, v
        return None, ""

    @staticmethod
    def _hex(value):
        m = re.search(r"0x([0-9a-f]+)", str(value or ""), re.I)
        return int(m.group(1), 16) if m else 0


def make_scanner():
    if IS_WINDOWS:
        return WindowsScanner()
    if IS_MAC:
        return MacScanner()
    return None


# ==========================================================================
# Verdicts
# ==========================================================================
@dataclass
class Verdict:
    role: str       # success | warning | danger | text_muted
    title: str
    detail: str
    label: str = ""  # what to write on the cable: 10G, 5G, USB2, CHARGE


def usb_version(bcd):
    return "{}.{}".format(bcd >> 8, (bcd >> 4) & 0xF) if bcd else ""


def assess(d):
    """What this connection says about the cable."""
    if d.status != 1:
        return Verdict("danger", "Connection failed",
                       "The PC saw something plugged in but it didn't start ({}). A damaged or poor-quality "
                       "cable is a common cause; try another cable or port.".format(
                           CONNECTION_STATUS.get(d.status, "status {}".format(d.status)).lower()), "BAD?")
    speed = d.speed
    if speed is None:
        return Verdict("text_muted", "Speed unknown", "The system didn't report a link speed for this device.")
    rate = SPEEDS[speed][0]

    if speed >= SUPER_PLUS:
        return Verdict("success", "Full-speed data cable · {}".format(rate),
                       "Connected at 10 Gbps or faster ({}). Good for big transfers, around {} MB/s "
                       "in real copies.".format(SPEEDS[speed][1], REAL_WORLD[speed]), "10G")
    if speed == SUPER:
        if d.ssp_capable:
            return Verdict("warning", "Limited to 5 Gbps",
                           "This device can do 10 Gbps but only got 5 Gbps. The cable is probably a "
                           "5 Gbps (USB 3.2 Gen 1) cable, or the port only does 5 Gbps.", "5G")
        return Verdict("success", "USB 3 data cable · 5 Gbps",
                       "Connected at 5 Gbps (about {} MB/s in real copies). This device tops out at 5 Gbps, "
                       "so test with a 10 Gbps SSD to see if the cable goes faster.".format(REAL_WORLD[SUPER]),
                       "5G+" if d.ssp_capable is False else "5G")
    if d.usb2_half:
        return Verdict("text_muted", "USB 2 side of a USB 3 hub",
                       "Every USB 3 hub also shows up as a USB 2 hub for older devices. This is normal.")
    if d.ss_capable:
        if d.upstream is not None:
            if d.upstream.ss_capable:
                why = "The hub supports USB 3 too, so the cable between the PC and the hub is the likely culprit."
            else:
                why = "That hub only does USB 2, so plug this device in somewhere else to use its full speed."
            return Verdict("warning", "Behind a hub running at USB 2",
                           "This device supports USB 3, but it's connected through {} which is running at "
                           "480 Mbps. {}".format(d.upstream.name, why), "USB2?")
        where = "the cable" if d.port_usb3 else "the cable (or a USB 2-only port)"
        return Verdict("danger", "USB 2.0 / charging cable · 480 Mbps",
                       "This device supports USB 3 but only got 480 Mbps (about 40 MB/s). That's almost "
                       "always {}: phone charging cables usually have USB 2 wiring only.".format(where), "USB2")
    if d.upstream is not None and d.ss_capable is None:
        return Verdict("text_muted", "{} · behind a USB 2 hub".format(rate),
                       "It's plugged into a hub running at 480 Mbps ({}), so this can't test the cable's "
                       "USB 3 lanes.".format(d.upstream.name), "USB2?")
    if d.ss_capable is False or speed <= HIGH:
        if IS_MAC and d.ss_capable is None:
            return Verdict("warning", "USB 2 speed · {}".format(rate),
                           "Connected at 480 Mbps or slower. If this is a USB 3 drive, the cable is the "
                           "likely limit. (macOS doesn't say what the device could do.)", "USB2?")
        return Verdict("text_muted", "Data works · USB 2 device",
                       "The cable carries data, but this device only speaks USB 2 ({}), so the fast lanes "
                       "weren't tested. Test with an SSD or USB 3 drive.".format(rate), "DATA")
    return Verdict("text_muted", rate, "")


def charge_only_verdict(seconds):
    return Verdict("danger", "No data connection · charge-only cable?",
                   "Nothing showed up in {} seconds. If the device lit up or started charging, the cable "
                   "has no data wires (charge-only). Also check that the device is switched on, and that "
                   "phones are unlocked and set to file transfer.".format(seconds), "CHARGE")


def describe(d):
    """One line of specs for the device list."""
    parts = []
    if d.manufacturer and d.manufacturer.lower() not in d.name.lower():
        parts.append(d.manufacturer)
    if d.vid or d.pid:
        parts.append("{:04X}:{:04X}".format(d.vid, d.pid))
    if d.ssp_capable:
        parts.append("10 Gbps device")
    elif d.ss_capable:
        parts.append("USB 3 device")
    elif d.ss_capable is False and d.bcd_usb:
        parts.append("USB {} device".format(usb_version(d.bcd_usb)))
    if d.service.lower() == "uaspstor":
        parts.append("UAS (fast protocol)")
    elif d.service.lower() == "usbstor":
        parts.append("BOT (slower protocol)")
    if d.type_c is not None:
        parts.append("Type-C port" if d.type_c else "Type-A port")
    if d.port_usb3 is False:
        parts.append("USB 2 port")
    return "  ·  ".join(parts)


def pick_primary(devices):
    """The device a cable test is about: skip hubs and internal gear, prefer the most capable."""
    def score(d):
        return (d.status != 1, not d.is_hub, bool(d.ss_capable), bool(d.ssp_capable), d.speed or 0)
    return max(devices, key=score) if devices else None


# ==========================================================================
# Drive speed test
# ==========================================================================
def list_drives():
    try:
        import psutil
        parts = psutil.disk_partitions()
    except Exception:
        return []
    drives = []
    for p in parts:
        if "cdrom" in p.opts.lower() or not p.fstype:
            continue
        if IS_MAC and not p.mountpoint.startswith("/Volumes/"):
            continue
        drives.append((_volume_label(p.mountpoint), p.mountpoint))
    return drives


def _volume_label(mount):
    """E.g. "E:\\ (LaCie Rugged)" on Windows, the volume name on macOS."""
    if IS_MAC:
        return os.path.basename(mount.rstrip("/")) or mount
    if IS_WINDOWS:
        name = ctypes.create_unicode_buffer(261)
        if ctypes.windll.kernel32.GetVolumeInformationW(mount, name, 261, None, None, None, None, 0) and name.value:
            return "{} ({})".format(mount, name.value)
    return mount


def _is_system_drive(mount):
    system = os.environ.get("SystemDrive", "C:") + "\\" if IS_WINDOWS else "/"
    return os.path.normcase(mount) == os.path.normcase(system)


def speed_test(folder, size_mb, progress, cancel):
    """Write then read a test file with the OS cache bypassed. Returns (write MB/s, read MB/s)."""
    path = os.path.join(folder, "_autoae_usb_speedtest.tmp")
    chunk = 8 * 1024 * 1024
    count = max(1, size_mb * 1024 * 1024 // chunk)
    try:
        if IS_WINDOWS:
            return _speed_test_windows(path, chunk, count, progress, cancel)
        return _speed_test_posix(path, chunk, count, progress, cancel)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _speed_test_windows(path, chunk, count, progress, cancel):
    NO_BUFFERING, WRITE_THROUGH, SEQUENTIAL = 0x20000000, 0x80000000, 0x08000000
    buf = _k32.VirtualAlloc(None, chunk, 0x3000, 0x04)  # page aligned, as unbuffered I/O requires
    if not buf:
        raise OSError("Couldn't allocate a test buffer")
    try:
        ctypes.memmove(buf, os.urandom(chunk), chunk)  # random data so compressing drives can't cheat
        done = wintypes.DWORD()

        handle = _k32.CreateFileW(path, GENERIC_WRITE, 0, None, CREATE_ALWAYS, NO_BUFFERING | WRITE_THROUGH, None)
        if handle in (None, INVALID_HANDLE):
            raise ctypes.WinError(ctypes.get_last_error())
        start = time.perf_counter()
        try:
            for i in range(count):
                if cancel():
                    return None
                if not _k32.WriteFile(handle, buf, chunk, ctypes.byref(done), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                progress((i + 1) / (count * 2))
            _k32.FlushFileBuffers(handle)
        finally:
            _k32.CloseHandle(handle)
        write = count * chunk / (time.perf_counter() - start) / 1e6

        handle = _k32.CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING,
                                  NO_BUFFERING | SEQUENTIAL, None)
        if handle in (None, INVALID_HANDLE):
            raise ctypes.WinError(ctypes.get_last_error())
        start = time.perf_counter()
        try:
            for i in range(count):
                if cancel():
                    return None
                if not _k32.ReadFile(handle, buf, chunk, ctypes.byref(done), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                progress(0.5 + (i + 1) / (count * 2))
        finally:
            _k32.CloseHandle(handle)
        read = count * chunk / (time.perf_counter() - start) / 1e6
        return write, read
    finally:
        _k32.VirtualFree(buf, 0, 0x8000)


def _speed_test_posix(path, chunk, count, progress, cancel):
    import fcntl
    no_cache = getattr(fcntl, "F_NOCACHE", 48 if IS_MAC else None)
    data = os.urandom(chunk)

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        if no_cache:
            fcntl.fcntl(fd, no_cache, 1)
        start = time.perf_counter()
        for i in range(count):
            if cancel():
                return None
            os.write(fd, data)
            progress((i + 1) / (count * 2))
        os.fsync(fd)
        write = count * chunk / (time.perf_counter() - start) / 1e6
    finally:
        os.close(fd)

    fd = os.open(path, os.O_RDONLY)
    try:
        if no_cache:
            fcntl.fcntl(fd, no_cache, 1)
        start = time.perf_counter()
        for i in range(count):
            if cancel():
                return None
            os.read(fd, chunk)
            progress(0.5 + (i + 1) / (count * 2))
        read = count * chunk / (time.perf_counter() - start) / 1e6
    finally:
        os.close(fd)
    return write, read


def interpret_speed(mb_s):
    if mb_s < 45:
        return "warning", "USB 2 territory. If this is an SSD, the cable (or port) is holding it back."
    if mb_s < 480:
        return "success", "Consistent with a 5 Gbps link (or a drive that's slower than the link)."
    if mb_s < 1150:
        return "success", "Consistent with a 10 Gbps link."
    if mb_s < 2200:
        return "success", "Consistent with a 20 Gbps link."
    return "success", "Thunderbolt / USB4 class speed."


# ==========================================================================
# Tested-cable history
# ==========================================================================
def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_history(entries):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
    except Exception as e:
        print("Failed to save cable history: {}".format(e))


# ==========================================================================
# UI
# ==========================================================================
TEST_SECONDS = 30
SETTLE_SECONDS = 2.5


class Tool(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "USB Cable Checker"
        self.description = "Find out if a USB cable is a fast data cable, USB 2 or charge-only"
        self.category = "Storage & Backup"
        self.icon = "usb"
        self.window = None

    # --- window -----------------------------------------------------------
    def create_window(self, parent):
        window = self.create_tool_window(parent, self.name, 900, 700)
        self.window = window
        self.scanner = make_scanner()
        self.devices = []
        self._scan_lock = threading.Lock()
        self._closed = False
        self._tick_job = None
        self._signature = None
        self._test = None            # {"baseline", "deadline", "settle_at"} while a cable test runs
        self._last_verdict = None
        self._speed_cancel = False
        self._speed_running = False

        page = theme.page(window)
        theme.tool_header(page, self).pack(fill="x", pady=(0, 12))

        self.tabs = ctk.CTkTabview(page, anchor="nw")
        self.tabs.pack(fill="both", expand=True)
        for name in ("Cable Test", "Connected", "Speed Test", "My Cables"):
            self.tabs.add(name)
        self._build_test_tab(self.tabs.tab("Cable Test"))
        self._build_devices_tab(self.tabs.tab("Connected"))
        self._build_speed_tab(self.tabs.tab("Speed Test"))
        self._build_history_tab(self.tabs.tab("My Cables"))

        if self.scanner is None:
            self._show_verdict(Verdict("text_muted", "Not supported here",
                                       "USB link detection works on Windows and macOS."))
            self.test_button.configure(state="disabled")
        else:
            self._tick()
        return window

    def close_window(self):
        self._closed = True
        self._speed_cancel = True
        if self._tick_job is not None:
            try:
                self.window.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None

    def _alive(self):
        return not self._closed and self.window is not None and self.window.winfo_exists()

    def _on_ui(self, fn, *args):
        if self._alive():
            try:
                self.window.after(0, lambda: self._alive() and fn(*args))
            except RuntimeError:
                pass  # window is going away

    # --- Cable Test tab -----------------------------------------------------
    def _build_test_tab(self, tab):
        steps = theme.Card(tab, title="How to test a cable")
        steps.pack(fill="x", pady=(4, 0))
        theme.muted(steps.body, role="body", wraplength=780, text=(
            "1.  Click Test a cable.\n"
            "2.  Plug the cable into this computer with a USB 3 device on the other end. "
            "A portable SSD is ideal: it shows the cable's full speed.\n"
            "3.  Wait a few seconds for the result, then give the cable a name and save it."
        )).pack(fill="x")

        result = theme.Card(tab, title="Result")
        result.pack(fill="both", expand=True, pady=(12, 0))
        top = ctk.CTkFrame(result.body, fg_color="transparent")
        top.pack(fill="x")
        self.label_chip = ctk.CTkLabel(top, text="?", width=86, height=62, corner_radius=12,
                                       font=theme.sized_font(22, "bold"), fg_color=C["surface_2"],
                                       text_color=C["text_muted"])
        self.label_chip.pack(side="left", padx=(0, 16))
        text = ctk.CTkFrame(top, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True)
        self.verdict_title = ctk.CTkLabel(text, text="Ready", font=theme.sized_font(18, "bold"), anchor="w")
        self.verdict_title.pack(fill="x")
        self.verdict_detail = theme.muted(text, role="body", wraplength=640,
                                          text="Click Test a cable, then plug it in.")
        self.verdict_detail.pack(fill="x")
        self.verdict_specs = theme.muted(result.body, text="")
        self.verdict_specs.pack(fill="x", pady=(12, 0))

        save_row = ctk.CTkFrame(result.body, fg_color="transparent")
        save_row.pack(fill="x", pady=(14, 0))
        self.test_progress = theme.ProgressBar(result.body)  # packed above save_row during a test
        self._save_row = save_row
        self.cable_name = ctk.CTkEntry(save_row, height=34, font=theme.font("body"),
                                       placeholder_text="Name this cable, e.g. \"Black braided 1 m\"")
        self.cable_name.pack(side="left", fill="x", expand=True)
        self.cable_name.bind("<Return>", lambda e: self.save_cable())
        self.save_button = theme.Button(save_row, "Save to My Cables", kind="secondary", width=150,
                                        command=self.save_cable, state="disabled")
        self.save_button.pack(side="left", padx=(8, 0))

        actions = theme.action_bar(tab, pady=(12, 0))
        self.cancel_button = theme.Button(actions, "Cancel", kind="ghost", width=90, command=self.cancel_test,
                                          state="disabled")
        self.cancel_button.pack(side="left")
        self.test_button = theme.Button(actions, "Test a cable", kind="primary", width=150, command=self.start_test)
        self.test_button.pack(side="right")

    def _show_verdict(self, verdict, specs=""):
        color = C[verdict.role]
        self.verdict_title.configure(text=verdict.title, text_color=color)
        self.verdict_detail.configure(text=verdict.detail)
        self.verdict_specs.configure(text=specs)
        self.label_chip.configure(text=verdict.label or "?", text_color=color)

    def start_test(self):
        if self._test is not None:
            return
        self._last_verdict = None
        self.save_button.configure(state="disabled")
        self.test_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.label_chip.configure(text="…", text_color=C["text_muted"])
        self.verdict_title.configure(text="Getting ready…", text_color=C["text"])
        self.verdict_detail.configure(text="Reading what's already plugged in.")
        self.verdict_specs.configure(text="")
        self._test = {"baseline": None, "deadline": None, "settle_at": None}
        self.test_progress.set(0)
        self.test_progress.pack(fill="x", pady=(12, 0), before=self._save_row)
        self._tick(now=True)

    def cancel_test(self):
        self._test = None
        self.test_progress.pack_forget()
        self.test_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self._show_verdict(Verdict("text_muted", "Ready", "Click Test a cable, then plug it in."))

    def _advance_test(self, devices):
        test = self._test
        now = time.monotonic()
        keys = {d.key for d in devices}
        if test["baseline"] is None:
            test["baseline"] = keys
            test["deadline"] = now + TEST_SECONDS
            self.verdict_title.configure(text="Plug the cable in now", text_color=C["accent_text"])
            self.verdict_detail.configure(text="Connect the cable with a device on the other end. "
                                               "Already plugged in? Unplug it and plug it back in.")
            return
        test["baseline"] &= keys  # unplugged during the test? it counts as new when it comes back
        new = [d for d in devices if d.key not in test["baseline"]]
        if new:
            if test["settle_at"] is None:
                test["settle_at"] = now + SETTLE_SECONDS  # let it finish connecting (USB 3 can retry at USB 2)
                self.verdict_title.configure(text="Device found, reading the link…", text_color=C["accent_text"])
                self.test_progress.set(0.9)
            elif now >= test["settle_at"]:
                self._finish_test(new)
            return
        test["settle_at"] = None
        left = test["deadline"] - now
        if left <= 0:
            self._finish_test([])
            return
        self.test_progress.set(1 - left / TEST_SECONDS)
        self.verdict_detail.configure(text="Waiting for a device… {} s. Connect the cable with a device on the "
                                           "other end. Already plugged in? Unplug it and plug it back in."
                                           .format(int(left)))

    def _finish_test(self, new):
        self._test = None
        self.test_progress.pack_forget()
        self.test_button.configure(state="normal", text="Test another")
        self.cancel_button.configure(state="disabled")
        primary = pick_primary(new)
        if primary is None:
            verdict, specs = charge_only_verdict(TEST_SECONDS), ""
        else:
            verdict = assess(primary)
            specs = primary.name
            if describe(primary):
                specs += "  ·  " + describe(primary)
            if len(new) > 1:
                specs += "  (+{} more device{} via a hub)".format(len(new) - 1, "s" if len(new) > 2 else "")
        self._last_verdict = verdict
        self._show_verdict(verdict, specs)
        self.save_button.configure(state="normal")
        self.cable_name.focus_set()

    # --- Connected tab ------------------------------------------------------
    def _build_devices_tab(self, tab):
        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.pack(fill="x", pady=(4, 8))
        self.devices_status = theme.muted(bar, text="Scanning…")
        self.devices_status.pack(side="left")
        theme.Button(bar, "Refresh", kind="secondary", width=100, image=theme.icon("refresh-cw", C["text"], 15),
                     compound="left", command=lambda: self._tick(now=True)).pack(side="right")
        self.show_all = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(bar, text="Show hubs & internal devices", variable=self.show_all, font=theme.font("body"),
                        checkbox_width=18, checkbox_height=18,
                        command=lambda: self._render_devices(force=True)).pack(side="right", padx=(0, 14))

        self.device_list = ctk.CTkScrollableFrame(tab, fg_color=C["field"], corner_radius=theme.RADIUS,
                                                  border_width=1, border_color=C["border"])
        self.device_list.pack(fill="both", expand=True)
        theme.muted(tab, text="Thunderbolt / USB4 drives connect over PCIe and may not appear here. "
                              "Use the Speed Test tab for those.").pack(fill="x", pady=(8, 0))

    def _render_devices(self, force=False):
        show_all = self.show_all.get()
        shown = [d for d in self.devices if show_all or (not d.is_hub and d.user_port is not False)]
        signature = (show_all, tuple((d.key, d.speed, d.status, d.name, d.ss_capable, d.upstream is not None) for d in shown))
        if signature == self._signature and not force:
            return
        self._signature = signature

        for child in self.device_list.winfo_children():
            child.destroy()
        hidden = len(self.devices) - len(shown)
        self.devices_status.configure(text="{} device{} connected{}".format(
            len(shown), "" if len(shown) == 1 else "s", "  ·  {} hubs/internal hidden".format(hidden) if hidden else ""))
        if not shown:
            theme.muted(self.device_list, role="body", text="Nothing plugged in.").pack(padx=14, pady=14, anchor="w")
            return
        for d in sorted(shown, key=lambda d: (-(d.speed or 0), d.name.lower())):
            self._device_row(d)

    def _device_row(self, d):
        verdict = assess(d)
        row = ctk.CTkFrame(self.device_list, fg_color=C["surface"], corner_radius=10, border_width=1,
                           border_color=C["border"])
        row.pack(fill="x", padx=6, pady=4)
        row.grid_columnconfigure(0, weight=1)
        name = d.name + ("  (hub)" if d.is_hub else "")
        ctk.CTkLabel(row, text=name, font=theme.font("heading"), anchor="w").grid(
            row=0, column=0, sticky="w", padx=14, pady=(10, 0))
        rate = SPEEDS[d.speed][0] if d.speed is not None else (d.speed_text or "?")
        ctk.CTkLabel(row, text=rate, font=theme.font("body_strong"), text_color=C[verdict.role],
                     fg_color=C["surface_2"], corner_radius=8, width=90, height=26).grid(
            row=0, column=1, rowspan=2, padx=14, pady=(10, 0), sticky="ne")
        specs = describe(d)
        if specs:
            theme.muted(row, text=specs).grid(row=1, column=0, sticky="w", padx=14)
        ctk.CTkLabel(row, text=verdict.title, font=theme.font("body_strong"), text_color=C[verdict.role],
                     anchor="w").grid(row=2, column=0, columnspan=2, sticky="w", padx=14, pady=(4, 0))
        if verdict.detail:
            theme.muted(row, text=verdict.detail, wraplength=720).grid(
                row=3, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 10))

    # --- scanning -----------------------------------------------------------
    def _tick(self, now=False):
        """Rescan every few seconds (faster during a test). Scans run off the UI thread."""
        if not self._alive():
            return
        if self._tick_job is not None:
            try:
                self.window.after_cancel(self._tick_job)
            except Exception:
                pass
        if not self._scan_lock.locked():
            threading.Thread(target=self._scan_worker, daemon=True).start()
        self._tick_job = self.window.after(1000 if self._test else 3000, self._tick)

    def _scan_worker(self):
        if not self._scan_lock.acquire(blocking=False):
            return
        try:
            devices = self.scanner.scan()
        except Exception as e:
            devices = None
            self._on_ui(self.devices_status.configure, text="Error: couldn't read USB devices ({})".format(e))
        finally:
            self._scan_lock.release()
        if devices is not None:
            self._on_ui(self._on_scan, devices)

    def _on_scan(self, devices):
        self.devices = devices
        self._render_devices()
        if self._test is not None:
            self._advance_test(devices)

    # --- Speed Test tab -----------------------------------------------------
    def _build_speed_tab(self, tab):
        card = theme.Card(tab, title="Drive speed test",
                          subtitle="Writes then reads a temporary file on the drive, bypassing the cache, "
                                   "and deletes it afterwards.")
        card.pack(fill="x", pady=(4, 0))
        self.drive_menu = ctk.CTkOptionMenu(card.body, values=["No drives found"], width=260, height=34,
                                            dynamic_resizing=False)
        self._refresh_drives()
        theme.form_row(card.body, 0, "Drive", self.drive_menu, sticky="w")
        self.size_menu = ctk.CTkSegmentedButton(card.body, values=["256 MB", "1 GB", "4 GB"])
        self.size_menu.set("1 GB")
        theme.form_row(card.body, 1, "Test size", self.size_menu, sticky="w")

        results = theme.Card(tab, title="Results")
        results.pack(fill="both", expand=True, pady=(12, 0))
        numbers = ctk.CTkFrame(results.body, fg_color="transparent")
        numbers.pack(fill="x")
        self.write_value = self._stat(numbers, "Write")
        self.read_value = self._stat(numbers, "Read")
        self.speed_note = theme.muted(results.body, role="body", wraplength=780, text="")
        self.speed_note.pack(fill="x", pady=(10, 0))
        self.speed_progress = theme.ProgressBar(results.body)
        self.speed_progress.pack(fill="x", pady=(12, 0))
        theme.muted(results.body, wraplength=780, text=(
            "Typical real-world copy speeds:  USB 2 ≈ 40 MB/s   ·   5 Gbps ≈ 400 MB/s   ·   "
            "10 Gbps ≈ 1,000 MB/s   ·   20 Gbps ≈ 2,000 MB/s   ·   Thunderbolt / USB4 ≈ 2,800+ MB/s.\n"
            "Hard drives and SD cards are slower than the link, so they can't show a cable's full speed.")
        ).pack(fill="x", pady=(12, 0))

        actions = theme.action_bar(tab, pady=(12, 0))
        self.speed_button = theme.Button(actions, "Run speed test", kind="primary", width=150,
                                         command=self.run_speed_test)
        self.speed_button.pack(side="right")
        theme.Button(actions, "Refresh drives", kind="ghost", width=120, command=self._refresh_drives).pack(side="left")

    def _stat(self, master, label):
        box = ctk.CTkFrame(master, fg_color=C["surface_2"], corner_radius=10)
        box.pack(side="left", padx=(0, 12))
        theme.overline(box, label).pack(anchor="w", padx=16, pady=(10, 0))
        value = ctk.CTkLabel(box, text="–", font=theme.sized_font(26, "bold"), anchor="w", width=180)
        value.pack(anchor="w", padx=16, pady=(0, 10))
        return value

    def _refresh_drives(self):
        self.drive_map = dict(list_drives())
        names = list(self.drive_map) or ["No drives found"]
        self.drive_menu.configure(values=names)
        if self.drive_menu.get() not in self.drive_map:
            self.drive_menu.set(next((n for n, p in self.drive_map.items() if not _is_system_drive(p)), names[0]))

    def run_speed_test(self):
        if self._speed_running:
            self._speed_cancel = True
            return
        drive = self.drive_map.get(self.drive_menu.get(), "")
        if not os.path.isdir(drive):
            self.speed_note.configure(text="Pick a drive first.", text_color=C["warning"])
            return
        size_mb = {"256 MB": 256, "1 GB": 1024, "4 GB": 4096}[self.size_menu.get() or "1 GB"]
        try:
            import shutil
            if shutil.disk_usage(drive).free < size_mb * 1024 * 1024 * 1.1:
                self.speed_note.configure(text="Not enough free space on {} for a {} test.".format(
                    drive, self.size_menu.get()), text_color=C["warning"])
                return
        except OSError:
            pass
        self._speed_running, self._speed_cancel = True, False
        self.speed_button.configure(text="Stop")
        self.write_value.configure(text="…")
        self.read_value.configure(text="…")
        self.speed_note.configure(text="Testing {}…".format(drive), text_color=C["text_muted"])
        threading.Thread(target=self._speed_worker, args=(drive, size_mb), daemon=True).start()

    def _speed_worker(self, drive, size_mb):
        last = [0.0]

        def progress(value):
            if time.monotonic() - last[0] > 0.1:
                last[0] = time.monotonic()
                self._on_ui(self.speed_progress.set, value)

        try:
            result = speed_test(drive, size_mb, progress, lambda: self._speed_cancel)
            error = None
        except Exception as e:
            result, error = None, e
        self._on_ui(self._speed_done, drive, result, error)

    def _speed_done(self, drive, result, error):
        self._speed_running = False
        self.speed_button.configure(text="Run speed test")
        self.speed_progress.set(0)
        if error is not None:
            self.write_value.configure(text="–")
            self.read_value.configure(text="–")
            self.speed_note.configure(text="Error: {}".format(error), text_color=C["danger"])
            return
        if result is None:
            self.write_value.configure(text="–")
            self.read_value.configure(text="–")
            self.speed_note.configure(text="Stopped.", text_color=C["text_muted"])
            return
        write, read = result
        self.write_value.configure(text="{:,.0f} MB/s".format(write))
        self.read_value.configure(text="{:,.0f} MB/s".format(read))
        role, note = interpret_speed(max(write, read))
        self.speed_note.configure(text="{}  {}".format(drive, note), text_color=C[role])

    # --- My Cables tab ------------------------------------------------------
    def _build_history_tab(self, tab):
        theme.muted(tab, role="body", text="Cables you've tested and named. Tip: put a matching label or "
                                           "colored tape on each cable.").pack(fill="x", pady=(4, 8))
        self.history_list = ctk.CTkScrollableFrame(tab, fg_color=C["field"], corner_radius=theme.RADIUS,
                                                   border_width=1, border_color=C["border"])
        self.history_list.pack(fill="both", expand=True)
        self._render_history()

    def _render_history(self):
        for child in self.history_list.winfo_children():
            child.destroy()
        entries = load_history()
        if not entries:
            theme.muted(self.history_list, role="body", text="No cables saved yet. Test one on the Cable Test tab."
                        ).pack(padx=14, pady=14, anchor="w")
            return
        for index, entry in reversed(list(enumerate(entries))):
            row = ctk.CTkFrame(self.history_list, fg_color=C["surface"], corner_radius=10, border_width=1,
                               border_color=C["border"])
            row.pack(fill="x", padx=6, pady=4)
            ctk.CTkLabel(row, text=entry.get("label") or "?", width=70, height=40, corner_radius=8,
                         fg_color=C["surface_2"], text_color=C[entry.get("role", "text_muted")],
                         font=theme.sized_font(15, "bold")).pack(side="left", padx=10, pady=8)
            text = ctk.CTkFrame(row, fg_color="transparent")
            text.pack(side="left", fill="x", expand=True, pady=6)
            ctk.CTkLabel(text, text=entry.get("name", "Unnamed cable"), font=theme.font("heading"),
                         anchor="w").pack(fill="x")
            theme.muted(text, text="{}  ·  tested {}{}".format(
                entry.get("result", ""), entry.get("date", ""),
                "  ·  with " + entry["device"] if entry.get("device") else "")).pack(fill="x")
            theme.Button(row, "", kind="ghost", width=34, image=theme.icon("trash-2", C["text_muted"], 16),
                         command=lambda i=index: self._delete_cable(i)).pack(side="right", padx=8)

    def save_cable(self):
        verdict = self._last_verdict
        if verdict is None:
            return
        name = self.cable_name.get().strip() or "Cable {}".format(len(load_history()) + 1)
        device = self.verdict_specs.cget("text").split("  ·  ")[0]
        entries = load_history()
        entries.append({"name": name, "label": verdict.label, "result": verdict.title, "role": verdict.role,
                        "device": device, "date": time.strftime("%Y-%m-%d")})
        save_history(entries)
        self.cable_name.delete(0, "end")
        self.save_button.configure(state="disabled")
        self.verdict_specs.configure(text="Saved as \"{}\" in My Cables.".format(name))
        self._render_history()

    def _delete_cable(self, index):
        entries = load_history()
        if 0 <= index < len(entries):
            del entries[index]
            save_history(entries)
        self._render_history()


# ==========================================================================
# Text report: python tools/usb_cable_checker_tool.py
# ==========================================================================
if __name__ == "__main__":
    scanner = make_scanner()
    if scanner is None:
        sys.exit("USB link detection works on Windows and macOS.")
    for dev in sorted(scanner.scan(), key=lambda d: d.key):
        v = assess(dev)
        rate = SPEEDS[dev.speed][0] if dev.speed is not None else "?"
        print("{}{:<42} {:>9}  {}".format("  " * (len(dev.key) - 4), dev.name[:42], rate, v.title))
        extra = describe(dev)
        if extra:
            print("{}{}".format("  " * (len(dev.key) - 3), extra))
