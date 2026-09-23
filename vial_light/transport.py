"""HID 传输层。

优先用 ``hidapi``（``pip install hidapi``）。如果没装，在 Windows 上自动退回到
纯 ``ctypes`` 调用系统的 SetupAPI / hid.dll —— 这样软件**零依赖**也能跑。

两种后端对外暴露同一套最小接口：

* :func:`enumerate_devices` -> ``[DeviceInfo]``
* :func:`open_device` -> 句柄对象，含 ``write`` / ``read`` / ``close``
"""

import sys
import time

PAGE_VIA_RAW = 0xFF60
USAGE_VIA_RAW = 0x61

_backend_name = None


# --------------------------------------------------------------------------
# 公共数据结构
# --------------------------------------------------------------------------
class DeviceInfo(object):
    """一个 HID 接口。``path`` 是后端原生的打开标识。"""

    __slots__ = (
        "path",
        "key",
        "vendor_id",
        "product_id",
        "usage_page",
        "usage",
        "serial",
        "manufacturer",
        "product",
        "interface",
        "input_len",
        "output_len",
    )

    def __init__(self, **kw):
        for name in self.__slots__:
            setattr(self, name, kw.get(name))

    @property
    def is_via_raw(self):
        return self.usage_page == PAGE_VIA_RAW and self.usage == USAGE_VIA_RAW

    @property
    def display_name(self):
        mfr = (self.manufacturer or "").strip()
        prod = (self.product or "").strip()
        if mfr and prod:
            name = "%s %s" % (mfr, prod)
        else:
            name = prod or mfr or "未知 HID 设备"
        return "%s  [%04X:%04X]" % (name, self.vendor_id or 0, self.product_id or 0)

    def __repr__(self):
        return "<DeviceInfo %s usage=%s:%s>" % (
            self.display_name,
            hex(self.usage_page) if self.usage_page is not None else "?",
            hex(self.usage) if self.usage is not None else "?",
        )


# --------------------------------------------------------------------------
# 后端选择
# --------------------------------------------------------------------------
def backend_name():
    global _backend_name
    if _backend_name is None:
        _backend_name = _detect_backend()
    return _backend_name


_hidapi = None


def _detect_backend():
    global _hidapi
    try:
        import hid as _h

        _hidapi = _h
        return "hidapi"
    except ImportError:
        pass
    if sys.platform == "win32":
        try:
            _win_self_test()
            return "ctypes-win"
        except Exception:
            pass
    return None


# --------------------------------------------------------------------------
# hidapi 后端
# --------------------------------------------------------------------------
class _HidapiHandle(object):
    def __init__(self, path):
        import hid

        self._dev = hid.device()
        self._dev.open_path(path)

    def write(self, data):
        return self._dev.write(bytes(data))

    def read(self, size, timeout_ms=500):
        return bytes(self._dev.read(size, timeout_ms=timeout_ms) or b"")

    def close(self):
        try:
            self._dev.close()
        except Exception:
            pass


def _hidapi_enumerate():
    import hid

    out = []
    for d in hid.enumerate():
        path = d.get("path")
        key = path.decode("utf-8", "replace") if isinstance(path, bytes) else str(path)
        out.append(
            DeviceInfo(
                path=path,
                key=key,
                vendor_id=d.get("vendor_id"),
                product_id=d.get("product_id"),
                usage_page=d.get("usage_page"),
                usage=d.get("usage"),
                serial=d.get("serial_number"),
                manufacturer=d.get("manufacturer_string"),
                product=d.get("product_string"),
                interface=d.get("interface_number"),
                input_len=None,
                output_len=None,
            )
        )
    return out


# --------------------------------------------------------------------------
# 纯 ctypes（Windows）后端
# --------------------------------------------------------------------------
_WIN = {}


def _win_self_test():
    """确认 hid.dll / setupapi.dll 可用。"""
    _win_load()


def _win_load():
    if _WIN:
        return _WIN
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("InterfaceClassGuid", GUID),
            ("Flags", wintypes.DWORD),
            ("Reserved", ctypes.c_void_p),
        ]

    class HIDD_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Size", wintypes.ULONG),
            ("VendorID", wintypes.USHORT),
            ("ProductID", wintypes.USHORT),
            ("VersionNumber", wintypes.USHORT),
        ]

    class HIDP_CAPS(ctypes.Structure):
        _fields_ = [
            ("Usage", wintypes.USHORT),
            ("UsagePage", wintypes.USHORT),
            ("InputReportByteLength", wintypes.USHORT),
            ("OutputReportByteLength", wintypes.USHORT),
            ("FeatureReportByteLength", wintypes.USHORT),
            ("Reserved", wintypes.USHORT * 17),
            ("NumberLinkCollectionNodes", wintypes.USHORT),
            ("NumberInputButtonCaps", wintypes.USHORT),
            ("NumberInputValueCaps", wintypes.USHORT),
            ("NumberInputDataIndices", wintypes.USHORT),
            ("NumberOutputButtonCaps", wintypes.USHORT),
            ("NumberOutputValueCaps", wintypes.USHORT),
            ("NumberOutputDataIndices", wintypes.USHORT),
            ("NumberFeatureButtonCaps", wintypes.USHORT),
            ("NumberFeatureValueCaps", wintypes.USHORT),
            ("NumberFeatureDataIndices", wintypes.USHORT),
        ]

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    hid_dll = ctypes.WinDLL("hid", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(GUID),
        wintypes.LPCWSTR,
        wintypes.HWND,
        wintypes.DWORD,
    ]
    setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(GUID),
        wintypes.DWORD,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ]
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]

    hid_dll.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
    hid_dll.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
    hid_dll.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    hid_dll.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    hid_dll.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
    hid_dll.HidD_SetNumInputBuffers.argtypes = [wintypes.HANDLE, wintypes.ULONG]
    for fn in ("HidD_GetSerialNumberString", "HidD_GetManufacturerString", "HidD_GetProductString"):
        getattr(hid_dll, fn).argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]

    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    # 必须显式声明 argtypes：否则 64 位的 HANDLE 会被当成 32 位 int 传进去而截断
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetOverlappedResult.restype = wintypes.BOOL
    kernel32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(OVERLAPPED),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
    ]
    kernel32.CancelIoEx.restype = wintypes.BOOL
    kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]
    for fn_name in ("ReadFile", "WriteFile"):
        fn = getattr(kernel32, fn_name)
        fn.restype = wintypes.BOOL
        fn.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(OVERLAPPED),
        ]

    _WIN.update(
        dict(
            ctypes=ctypes,
            wintypes=wintypes,
            GUID=GUID,
            SP_DEVICE_INTERFACE_DATA=SP_DEVICE_INTERFACE_DATA,
            HIDD_ATTRIBUTES=HIDD_ATTRIBUTES,
            HIDP_CAPS=HIDP_CAPS,
            OVERLAPPED=OVERLAPPED,
            setupapi=setupapi,
            hid=hid_dll,
            kernel32=kernel32,
            INVALID=ctypes.c_void_p(-1).value,
        )
    )
    return _WIN


def _win_open_path(path):
    w = _win_load()
    k32 = w["kernel32"]
    handle = k32.CreateFileW(
        path,
        0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
        0x1 | 0x2,  # FILE_SHARE_READ | FILE_SHARE_WRITE
        None,
        3,  # OPEN_EXISTING
        0x40000000,  # FILE_FLAG_OVERLAPPED —— 带超时的读写必须用它
        None,
    )
    if not handle or handle == w["INVALID"]:
        return None
    return handle


def _win_string(handle, fn, length=260):
    w = _win_load()
    buf = w["ctypes"].create_unicode_buffer(length)
    ok = getattr(w["hid"], fn)(handle, w["ctypes"].cast(buf, w["ctypes"].c_void_p), length * 2)
    if not ok:
        return ""
    return buf.value


#: VIA / Vial 的有效载荷固定 32 字节；Windows HID 报文会多 1 字节 report ID
VIA_PAYLOAD = 32

_ERROR_IO_PENDING = 997
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258


def _win_caps(handle):
    w = _win_load()
    ctypes = w["ctypes"]
    preparsed = ctypes.c_void_p()
    if not w["hid"].HidD_GetPreparsedData(handle, ctypes.byref(preparsed)) or not preparsed:
        return None
    try:
        caps = w["HIDP_CAPS"]()
        if w["hid"].HidP_GetCaps(preparsed, ctypes.byref(caps)) < 0:
            return None
        return caps
    finally:
        w["hid"].HidD_FreePreparsedData(preparsed)


class _CtypesHandle(object):
    def __init__(self, path, input_len=33):
        self._handle = _win_open_path(path)
        if self._handle is None:
            raise OSError("无法打开 HID 设备: %s" % path)
        w = _win_load()
        self._ctypes = w["ctypes"]
        self._wintypes = w["wintypes"]
        self._k32 = w["kernel32"]
        self._input_len = max(int(input_len or (VIA_PAYLOAD + 1)), VIA_PAYLOAD + 1)
        try:
            w["hid"].HidD_SetNumInputBuffers(self._handle, 64)
        except Exception:
            pass

    def _overlapped_call(self, fn, buf, size, timeout_ms):
        """带超时的 WriteFile / ReadFile。返回实际字节数，失败返回 -1。"""
        ctypes = self._ctypes
        w = _win_load()
        event = self._k32.CreateEventW(None, True, False, None)
        if not event:
            return -1
        ov = w["OVERLAPPED"]()
        ov.hEvent = event
        got = self._wintypes.DWORD(0)
        try:
            ok = fn(self._handle, buf, size, ctypes.byref(got), ctypes.byref(ov))
            if ok:
                return got.value
            err = ctypes.get_last_error()
            if err != _ERROR_IO_PENDING:
                return -1
            rc = self._k32.WaitForSingleObject(event, int(timeout_ms))
            if rc == _WAIT_TIMEOUT:
                self._k32.CancelIoEx(self._handle, ctypes.byref(ov))
                return -1
            if rc != _WAIT_OBJECT_0:
                return -1
            if not self._k32.GetOverlappedResult(self._handle, ctypes.byref(ov), ctypes.byref(got), False):
                return -1
            return got.value
        finally:
            self._k32.CloseHandle(event)

    def write(self, data):
        ctypes = self._ctypes
        data = bytes(data)
        if not data:
            return 0
        buf = ctypes.create_string_buffer(data, len(data))
        n = self._overlapped_call(self._k32.WriteFile, buf, len(data), 1500)
        if n < 0:
            raise OSError("WriteFile 失败 (err=%d)" % ctypes.get_last_error())
        return n

    def read(self, size, timeout_ms=500):
        ctypes = self._ctypes
        want = max(self._input_len, int(size) + 1)
        buf = ctypes.create_string_buffer(want)
        n = self._overlapped_call(self._k32.ReadFile, buf, want, int(timeout_ms))
        if n <= 0:
            return b""
        raw = buf.raw[:n]
        # 剥掉前导 report ID，统一成 32 字节有效载荷
        if len(raw) > VIA_PAYLOAD:
            raw = raw[len(raw) - VIA_PAYLOAD :]
        return raw

    def close(self):
        if self._handle:
            self._k32.CloseHandle(self._handle)
            self._handle = None


def _ctypes_enumerate():
    w = _win_load()
    ctypes = w["ctypes"]
    setupapi = w["setupapi"]

    guid = w["GUID"]()
    w["hid"].HidD_GetHidGuid(ctypes.byref(guid))
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, 0x02 | 0x10)
    if not hdev or hdev == w["INVALID"]:
        raise OSError("SetupDiGetClassDevs 失败")

    results = []
    try:
        iface = w["SP_DEVICE_INTERFACE_DATA"]()
        iface.cbSize = ctypes.sizeof(w["SP_DEVICE_INTERFACE_DATA"])
        index = 0
        while setupapi.SetupDiEnumDeviceInterfaces(
            hdev, None, ctypes.byref(guid), index, ctypes.byref(iface)
        ):
            index += 1
            detail = ctypes.create_string_buffer(1024)
            # SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize: 8 on x64, 6 on x86
            ctypes.cast(detail, ctypes.POINTER(ctypes.c_ulong))[0] = (
                8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            )
            need = w["wintypes"].DWORD()
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(iface), detail, ctypes.sizeof(detail), ctypes.byref(need), None
            ):
                continue
            path = ctypes.wstring_at(ctypes.addressof(detail) + 4)
            info = _ctypes_describe(path)
            if info is not None:
                results.append(info)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return results


def _ctypes_describe(path):
    w = _win_load()
    ctypes = w["ctypes"]
    handle = _win_open_path(path)
    if handle is None:
        return None
    try:
        attrs = w["HIDD_ATTRIBUTES"]()
        attrs.Size = ctypes.sizeof(w["HIDD_ATTRIBUTES"])
        vid = pid = 0
        if w["hid"].HidD_GetAttributes(handle, ctypes.byref(attrs)):
            vid, pid = attrs.VendorID, attrs.ProductID
        caps = _win_caps(handle)
        usage_page = caps.UsagePage if caps else None
        usage = caps.Usage if caps else None
        in_len = caps.InputReportByteLength if caps else None
        out_len = caps.OutputReportByteLength if caps else None
        return DeviceInfo(
            path=path,
            key=path,
            vendor_id=vid,
            product_id=pid,
            usage_page=usage_page,
            usage=usage,
            serial=_win_string(handle, "HidD_GetSerialNumberString"),
            manufacturer=_win_string(handle, "HidD_GetManufacturerString"),
            product=_win_string(handle, "HidD_GetProductString"),
            interface=None,
            input_len=in_len,
            output_len=out_len,
        )
    finally:
        w["kernel32"].CloseHandle(handle)


# --------------------------------------------------------------------------
# 统一入口
# --------------------------------------------------------------------------
def enumerate_devices():
    """列出所有 HID 接口。"""
    name = backend_name()
    if name == "hidapi":
        return _hidapi_enumerate()
    if name == "ctypes-win":
        return _ctypes_enumerate()
    raise RuntimeError("没有可用的 HID 后端：请 pip install hidapi，或在 Windows 上运行")


def enumerate_via_raw():
    """只列出 usage page 0xFF60 / usage 0x61 的接口（QMK 的 raw HID）。"""
    return [d for d in enumerate_devices() if d.is_via_raw]


def open_device(info):
    """按 :class:`DeviceInfo` 打开设备，返回句柄对象。"""
    name = backend_name()
    if name == "hidapi":
        return _HidapiHandle(info.path)
    if name == "ctypes-win":
        return _CtypesHandle(info.path, info.input_len or (VIA_PAYLOAD + 1))
    raise RuntimeError("没有可用的 HID 后端")


def backend_description():
    name = backend_name()
    if name == "hidapi":
        try:
            import hid

            return "hidapi %s" % getattr(hid, "__version__", "")
        except Exception:
            return "hidapi"
    if name == "ctypes-win":
        return "系统内置 HID (ctypes)"
    return "不可用"


def sleep_ms(ms):
    time.sleep(ms / 1000.0)
