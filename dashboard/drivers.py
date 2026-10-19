"""
USB-serial driver helper for the dashboard (Windows only, standard library only).

* detect()  - lists the USB devices that look like a serial adapter (CH340, CP210x, FTDI, Arduino ...)
              with their driver state: installed / missing / problem / unknown.
* rescan()  - asks Windows to look for hardware changes (pnputil /scan-devices); Windows Update
              may then install the driver by itself (needs internet).
* local_items() / install() - install a driver you downloaded yourself and put in the drivers/ folder.
              Installing needs administrator rights: Windows shows its UAC prompt and YOU must approve it.

Nothing is downloaded or run behind your back: the dashboard only opens the vendor's page for you.
"""
import hashlib
import json
import os
import re
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVERS_DIR = os.path.join(BASE, "drivers")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# VID -> chip, vendor page. Pages are the vendors' own download pages; check that the page matches your chip.
VENDORS = {
    "1A86": {"chip": "WCH CH340 / CH341", "vendor": "WCH (Nanjing Qinheng)",
             "url": "https://www.wch-ic.com/downloads/CH341SER_EXE.html",
             "note": "Most Uno clones use this chip. Windows Update often installs it automatically."},
    "10C4": {"chip": "Silicon Labs CP210x", "vendor": "Silicon Labs",
             "url": "https://www.silabs.com/developer-tools/usb-to-uart-bridge-vcp-drivers", "note": ""},
    "0403": {"chip": "FTDI FT232 / FT231", "vendor": "FTDI",
             "url": "https://ftdichip.com/drivers/vcp-drivers/", "note": ""},
    "067B": {"chip": "Prolific PL2303", "vendor": "Prolific",
             "url": "https://www.prolific.com.tw/", "note": "Old PL2303 clones are rejected by newer drivers; use a CH340/FTDI board."},
    "2341": {"chip": "Arduino (ATmega16U2)", "vendor": "Arduino",
             "url": "https://www.arduino.cc/en/software",
             "note": "Windows has a built-in driver for genuine boards; installing the Arduino IDE adds the Arduino drivers."},
    "2A03": {"chip": "Arduino (arduino.org)", "vendor": "Arduino",
             "url": "https://www.arduino.cc/en/software", "note": ""},
}
SERIAL_WORDS = re.compile(r"serial|ch34|cp210|ftdi|arduino|uart|\(com\d+\)", re.I)
PS_DETECT = ("Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -like 'USB\\VID_*' } | "
             "Select-Object Name,PNPDeviceID,ConfigManagerErrorCode,Status,Service,PNPClass | ConvertTo-Json -Compress")


def _ps(script, timeout=25, env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                          capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW, env=e)


def classify(rows):
    """Turn Win32_PnPEntity rows into the serial-adapter list. Pure function (unit-tested without Windows)."""
    out = []
    for r in rows or []:
        pid_str = r.get("PNPDeviceID") or ""
        m = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", pid_str)
        if not m or "&MI_" in pid_str.upper():
            continue
        vid, pid = m.group(1).upper(), m.group(2).upper()
        name, cls, code = r.get("Name") or "USB device", r.get("PNPClass") or "", r.get("ConfigManagerErrorCode") or 0
        if vid not in VENDORS and cls != "Ports" and not SERIAL_WORDS.search(name):
            continue
        com = re.search(r"\((COM\d+)\)", name)
        if code == 0 and cls == "Ports":
            state, hint = "installed", "Driver installed."
        elif code == 28:
            state, hint = "missing", "Windows has no driver for this device (Device Manager code 28)."
        elif code != 0:
            state, hint = "problem", f"The driver reports a problem (Device Manager code {code}). Try unplugging, rescanning or reinstalling."
        else:
            state, hint = "unknown", "Connected, but Windows does not list it as a COM port."
        v = VENDORS.get(vid, {})
        out.append({"name": name, "vid": vid, "pid": pid, "chip": v.get("chip", "unknown USB-serial chip"),
                    "vendor": v.get("vendor", ""), "url": v.get("url", ""), "note": v.get("note", ""),
                    "com": com.group(1) if com else "", "service": r.get("Service") or "",
                    "code": code, "state": state, "hint": hint})
    return out


def detect():
    if sys.platform != "win32":
        return {"supported": False, "devices": [], "error": "Driver helper is for Windows only."}
    try:
        p = _ps(PS_DETECT)
        raw = json.loads(p.stdout) if p.stdout.strip() else []
        return {"supported": True, "devices": classify(raw if isinstance(raw, list) else [raw]), "error": ""}
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return {"supported": True, "devices": [], "error": f"Could not query Windows: {e}"}


def rescan():
    if sys.platform != "win32":
        raise OSError("Windows only.")
    p = subprocess.run(["pnputil", "/scan-devices"], capture_output=True, text=True, timeout=40, creationflags=NO_WINDOW)
    out = (p.stdout or p.stderr or "").strip()
    if p.returncode == 0:
        return out
    if "denied" in out.lower():  # scanning needs administrator rights: ask Windows (UAC), the user must approve
        q = _ps("Start-Process -FilePath pnputil.exe -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList '/scan-devices'", timeout=120)
        if q.returncode != 0:
            raise OSError("Not started: the administrator prompt was cancelled. You can also use Device Manager > Action > Scan for hardware changes.")
        return "Scan requested (administrator)."
    raise OSError(out or "pnputil /scan-devices failed. Use Device Manager > Action > Scan for hardware changes.")


def _ensure_dir():
    os.makedirs(DRIVERS_DIR, exist_ok=True)
    readme = os.path.join(DRIVERS_DIR, "README.txt")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as fh:
            fh.write("Put the USB-serial driver you downloaded from the VENDOR'S OFFICIAL page here:\n"
                     "  - the installer (for example CH341SER.EXE), or\n"
                     "  - the unzipped driver folder (it contains .inf files).\n"
                     "Then open the dashboard > Settings > USB-serial driver and click Install.\n"
                     "Windows will ask for administrator approval (UAC). Nothing here is run automatically.\n")


def local_items():
    """Installers (.exe) and driver folders (with .inf) the user placed in drivers/."""
    _ensure_dir()
    items, exes = [], []
    for n in sorted(os.listdir(DRIVERS_DIR)):
        p = os.path.join(DRIVERS_DIR, n)
        if os.path.isfile(p) and n.lower().endswith(".exe"):
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            items.append({"name": n, "kind": "installer", "bytes": os.path.getsize(p), "sha256": h.hexdigest(),
                          "signature": "", "signer": ""})
            exes.append(p)
        elif os.path.isfile(p) and n.lower().endswith(".zip"):
            items.append({"name": n, "kind": "zip", "bytes": os.path.getsize(p), "sha256": "", "signature": "", "signer": ""})
        elif os.path.isdir(p):
            infs = [f for _, _, fs in os.walk(p) for f in fs if f.lower().endswith(".inf")]
            if infs:
                items.append({"name": n, "kind": "inf-folder", "bytes": 0, "infs": len(infs), "sha256": "", "signature": "", "signer": ""})
    if exes and sys.platform == "win32":  # Authenticode: who signed the installer (for the user to judge)
        try:
            p = _ps("$e=$env:FOG_FILES -split '\\|'; Get-AuthenticodeSignature -LiteralPath $e | "
                    "Select-Object Path,@{n='Status';e={[string]$_.Status}},@{n='Signer';e={$_.SignerCertificate.Subject}} | ConvertTo-Json -Compress",
                    env={"FOG_FILES": "|".join(exes)})
            sigs = json.loads(p.stdout) if p.stdout.strip() else []
            for s in sigs if isinstance(sigs, list) else [sigs]:
                for it in items:
                    if it["kind"] == "installer" and os.path.join(DRIVERS_DIR, it["name"]).lower() == str(s.get("Path", "")).lower():
                        it["signature"], it["signer"] = str(s.get("Status", "")), s.get("Signer") or ""
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return items


def install(name):
    """Run an installer / install an .inf folder from drivers/ with a UAC prompt. `name` must be listed by local_items()."""
    if sys.platform != "win32":
        raise OSError("Windows only.")
    item = next((i for i in local_items() if i["name"] == name), None)
    if not item or item["kind"] == "zip":
        raise ValueError("Choose an installer or an unzipped driver folder from the drivers/ list (unzip .zip files first).")
    path = os.path.join(DRIVERS_DIR, item["name"])
    if item["kind"] == "installer":
        script = "Start-Process -FilePath $env:FOG_PATH -Verb RunAs"
    else:
        script = ("Start-Process -FilePath pnputil.exe -Verb RunAs -Wait -WindowStyle Hidden "
                  "-ArgumentList '/add-driver',(Join-Path $env:FOG_PATH '*.inf'),'/subdirs','/install'")
    p = _ps(script, timeout=180, env={"FOG_PATH": path})
    if p.returncode != 0:
        msg = (p.stderr or "").strip().splitlines()
        raise OSError("Not started: " + (msg[0] if msg else "the administrator prompt was cancelled."))
    return "Approve the Windows administrator prompt, finish the installer, then click Rescan."
