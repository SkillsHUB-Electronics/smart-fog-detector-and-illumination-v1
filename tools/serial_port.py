"""
Minimal serial port access for Windows using only the Python standard library
(ctypes + winreg), so no "pip install pyserial" is needed.
If pyserial happens to be installed it is used instead.
"""
import sys

try:
    import serial as _pyserial  # optional
except ImportError:
    _pyserial = None


def list_ports():
    """Return [(port, driver)] e.g. [("COM7", "USBSER000")]. USBSER = genuine Arduino, CH341 = clone."""
    ports = []
    if sys.platform == "win32":
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
        except OSError:
            return ports
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            ports.append((value, name.rsplit("\\", 1)[-1]))
            i += 1
    elif _pyserial:
        from serial.tools import list_ports as lp
        ports = [(p.device, p.description) for p in lp.comports()]
    return sorted(ports, key=lambda p: int("".join(c for c in p[0] if c.isdigit()) or 0))


class _WinSerial:
    def __init__(self, port, baud):
        import ctypes
        from ctypes import wintypes as wt
        self._ct, self._wt = ctypes, wt
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.restype = wt.HANDLE
        k.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE]
        k.ReadFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
        k.WriteFile.argtypes = [wt.HANDLE, wt.LPCVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
        k.CloseHandle.argtypes = [wt.HANDLE]
        self._k = k

        class DCB(ctypes.Structure):
            _fields_ = [("DCBlength", wt.DWORD), ("BaudRate", wt.DWORD), ("flags", wt.DWORD),
                        ("wReserved", wt.WORD), ("XonLim", wt.WORD), ("XoffLim", wt.WORD),
                        ("ByteSize", ctypes.c_ubyte), ("Parity", ctypes.c_ubyte), ("StopBits", ctypes.c_ubyte),
                        ("XonChar", ctypes.c_char), ("XoffChar", ctypes.c_char), ("ErrorChar", ctypes.c_char),
                        ("EofChar", ctypes.c_char), ("EvtChar", ctypes.c_char), ("wReserved1", wt.WORD)]

        class TIMEOUTS(ctypes.Structure):
            _fields_ = [(n, wt.DWORD) for n in ("ReadIntervalTimeout", "ReadTotalTimeoutMultiplier",
                                                  "ReadTotalTimeoutConstant", "WriteTotalTimeoutMultiplier",
                                                  "WriteTotalTimeoutConstant")]

        h = k.CreateFileW("\\\\.\\" + port, 0x80000000 | 0x40000000, 0, None, 3, 0, None)
        if h is None or h == wt.HANDLE(-1).value:
            err = ctypes.get_last_error()
            hint = " (port is busy: close the Arduino Serial Monitor)" if err == 5 else ""
            raise OSError(f"Cannot open {port}: Windows error {err}{hint}")
        self._h = h
        dcb = DCB()
        dcb.DCBlength = ctypes.sizeof(DCB)
        k.GetCommState(h, ctypes.byref(dcb))
        dcb.BaudRate = baud
        dcb.flags = 0x0001 | (1 << 4) | (1 << 12)  # binary, DTR on, RTS on (Uno resets like Serial Monitor)
        dcb.ByteSize, dcb.Parity, dcb.StopBits = 8, 0, 0
        if not k.SetCommState(h, ctypes.byref(dcb)):
            self.close()
            raise OSError(f"Cannot configure {port}")
        to = TIMEOUTS(0xFFFFFFFF, 0, 0, 0, 1000)  # reads return immediately with what is available
        k.SetCommTimeouts(h, ctypes.byref(to))

    def read(self):
        buf = self._ct.create_string_buffer(512)
        n = self._wt.DWORD(0)
        if not self._k.ReadFile(self._h, buf, 512, self._ct.byref(n), None):
            raise OSError("Serial read failed (board unplugged?)")
        return buf.raw[: n.value]

    def write(self, data):
        n = self._wt.DWORD(0)
        self._k.WriteFile(self._h, data, len(data), self._ct.byref(n), None)

    def close(self):
        if getattr(self, "_h", None):
            self._k.CloseHandle(self._h)
            self._h = None


class _PySerial:
    def __init__(self, port, baud):
        self._s = _pyserial.Serial(port, baud, timeout=0)

    def read(self):
        return self._s.read(512)

    def write(self, data):
        self._s.write(data)

    def close(self):
        self._s.close()


class LineReader:
    """Open a port and yield complete text lines without blocking."""

    def __init__(self, port, baud=115200):
        if _pyserial:
            self._p = _PySerial(port, baud)
        elif sys.platform == "win32":
            self._p = _WinSerial(port, baud)
        else:
            raise RuntimeError("Install pyserial: pip install pyserial")
        self._buf = b""

    def lines(self):
        """Return the list of complete lines received so far (may be empty)."""
        self._buf += self._p.read()
        *done, self._buf = self._buf.split(b"\n")
        return [d.decode("ascii", "ignore").strip() for d in done]

    def send(self, text):
        self._p.write(text.encode("ascii"))

    def close(self):
        self._p.close()


def choose_port(requested=None):
    if requested:
        return requested
    ports = list_ports()
    if not ports:
        sys.exit("No COM port found. Plug in the Arduino (and install the CH340 driver for clone boards).")
    if len(ports) == 1:
        return ports[0][0]
    print("Available ports:")
    for i, (p, drv) in enumerate(ports, 1):
        tag = "  <- Arduino?" if drv.upper().startswith(("USBSER", "CH341", "SILABSER")) else ""
        print(f"  {i}. {p}  ({drv}){tag}")
    while True:
        ans = input("Choose port number: ").strip()
        if ans.isdigit() and 1 <= int(ans) <= len(ports):
            return ports[int(ans) - 1][0]
        if ans.upper().startswith("COM"):
            return ans.upper()


if __name__ == "__main__":
    for p, d in list_ports():
        print(p, d)
