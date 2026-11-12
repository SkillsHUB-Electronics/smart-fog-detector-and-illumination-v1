"""
Calibration file, firmware upload and model learning for the dashboard (standard library only).

* calibration.h : read / validate / write firmware/fog_illumination_uno/calibration.h
* JOBS          : one background job at a time (firmware upload, model training, model install)
                  with a live log the page polls.
* upload        : compile + upload with arduino-cli (the one bundled with Arduino IDE 2, or on PATH).
                  The serial port is released first and re-opened afterwards.
* learning      : runs tools/train_model.py exactly like TRAIN_MODEL.bat and parses its report.

Nothing here runs without a click on the page, and the server only accepts local requests.
"""
import csv
import glob
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW_DIR = os.path.join(BASE, "firmware", "fog_illumination_uno")
CAL_PATH = os.path.join(FW_DIR, "calibration.h")
MODEL_PATH = os.path.join(FW_DIR, "model_weights.h")
DATA_DIR = os.path.join(BASE, "data")
BACKUP_DIR = os.path.join(BASE, "backups")
TRAIN = os.path.join(BASE, "tools", "train_model.py")
FQBN = "arduino:avr:uno"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CLASSES = ["clear", "light_fog", "dense_fog"]
LIGHT_CLASSES = ["day", "twilight", "night"]

# key -> (define name, min, max, default, is_int). Same defaults as calibration.h.
CAL_FIELDS = {
    "ldrLo": ("CAL_LDR_LO", 0, 900, 0, True),
    "ldrHi": ("CAL_LDR_HI", 100, 1023, 1023, True),
    "tGain": ("CAL_TEMP_GAIN", 0.5, 2.0, 1.0, False),
    "tOff": ("CAL_TEMP_OFFSET", -10.0, 10.0, 0.0, False),
    "hGain": ("CAL_HUM_GAIN", 0.5, 2.0, 1.0, False),
    "hOff": ("CAL_HUM_OFFSET", -30.0, 30.0, 0.0, False),
}
CAL_HEADER = """#pragma once
// ============================================================================
//  Sensor calibration (linearisation). Written by the dashboard (Calibration
//  page); you can also edit it by hand. Defaults = no correction.
//  After changing it, re-upload the firmware.
//
//    LDR light feature  = clamp((raw - CAL_LDR_LO) / (CAL_LDR_HI - CAL_LDR_LO), 0, 1)
//    temperature [C]    = DHT11 reading * CAL_TEMP_GAIN + CAL_TEMP_OFFSET
//    humidity [%RH]     = DHT11 reading * CAL_HUM_GAIN  + CAL_HUM_OFFSET   (limited to 1..100)
//  The laser baseline is stored in EEPROM (calibrate with the CAL button), not here.
// ============================================================================
"""
CAL_COMMENTS = {
    "CAL_LDR_LO": "LDR reading (ADC counts) in the darkest condition  -> 0 %",
    "CAL_LDR_HI": "LDR reading (ADC counts) in the brightest condition -> 100 %",
}


def _stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _backup(path, tag):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = os.path.join(BACKUP_DIR, f"{tag}_{_stamp()}{os.path.splitext(path)[1]}")
    shutil.copy2(path, dst)
    return dst


# ----------------------------------------------------------------------------- calibration.h
def read_calibration():
    vals = {k: v[3] for k, v in CAL_FIELDS.items()}
    try:
        with open(CAL_PATH, encoding="utf-8") as fh:
            txt = fh.read()
    except OSError:
        return vals
    for key, (name, _lo, _hi, _d, is_int) in CAL_FIELDS.items():
        m = re.search(r"^#define\s+" + name + r"\s+(-?[\d.]+)", txt, re.M)
        if m:
            vals[key] = int(float(m.group(1))) if is_int else float(m.group(1))
    return vals


def calibration_text(vals):
    lines = [CAL_HEADER.rstrip("\n")]
    for key, (name, _lo, _hi, _d, is_int) in CAL_FIELDS.items():
        v = f"{int(vals[key])}" if is_int else f"{float(vals[key]):.4f}f"
        c = CAL_COMMENTS.get(name)
        lines.append(f"#define {name:<16}{v}" + (f"   // {c}" if c else ""))
    return "\n".join(lines) + "\n"


def write_calibration(raw):
    """Validate the numbers from the page and write calibration.h (previous file is backed up)."""
    vals = {}
    for key, (_name, lo, hi, default, is_int) in CAL_FIELDS.items():
        v = raw.get(key, default)
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number")
        if v != v or not (lo <= v <= hi):
            raise ValueError(f"{key} must be between {lo} and {hi}")
        vals[key] = int(round(v)) if is_int else v
    if vals["ldrHi"] - vals["ldrLo"] < 50:
        raise ValueError("LDR upper limit must be at least 50 counts above the lower limit")
    backup = _backup(CAL_PATH, "calibration") if os.path.exists(CAL_PATH) else ""
    text = calibration_text(vals)
    with open(CAL_PATH, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return vals, text, backup


# ----------------------------------------------------------------------------- jobs
class Job:
    def __init__(self):
        self.lock = threading.RLock()
        self.kind = ""
        self.state = "idle"        # idle | running | ok | error
        self.lines = deque(maxlen=500)
        self.started = self.ended = 0.0
        self.result = {}
        self.seq = 0

    def log(self, text):
        with self.lock:
            self.lines.append(text.rstrip())
            self.seq += 1

    def start(self, kind, target, *args):
        with self.lock:
            if self.state == "running":
                raise RuntimeError(f"Another job is running ({self.kind}). Wait for it to finish.")
            self.kind, self.state, self.result = kind, "running", {}
            self.lines.clear()
            self.started, self.ended = time.time(), 0.0
            self.seq += 1
        threading.Thread(target=self._run, args=(target, args), daemon=True).start()

    def _run(self, target, args):
        try:
            res = target(self, *args) or {}
            state = "ok"
        except Exception as e:  # shown to the user in the log
            self.log(f"ERROR: {e}")
            res, state = {}, "error"
        with self.lock:
            self.result, self.state, self.ended = res, state, time.time()
            self.seq += 1

    def snapshot(self, tail=200):
        with self.lock:
            return {"kind": self.kind, "state": self.state, "lines": list(self.lines)[-tail:], "seq": self.seq,
                    "started": self.started, "ended": self.ended, "result": self.result}


JOB = Job()


def _run_cmd(job, cmd, cwd=None, timeout=900):
    job.log("$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    env = dict(os.environ, PYTHONUTF8="1")
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", creationflags=NO_WINDOW, env=env)
    t0, out = time.time(), []
    for line in p.stdout:
        out.append(line)
        job.log(line)
        if time.time() - t0 > timeout:
            p.kill()
            raise TimeoutError("timed out")
    p.wait()
    return p.returncode, "".join(out)


# ----------------------------------------------------------------------------- firmware upload
def find_cli():
    found = shutil.which("arduino-cli")
    if found:
        return found
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("LOCALAPPDATA", "") + r"\Programs"):
        p = os.path.join(root, "Arduino IDE", "resources", "app", "lib", "backend", "resources", "arduino-cli.exe")
        if os.path.exists(p):
            return p
    return ""


def _upload(job, hub, port, compile_only):
    cli = find_cli()
    if not cli:
        raise RuntimeError("arduino-cli not found. Install the Arduino IDE 2 (it contains arduino-cli) or upload from the IDE.")
    was = hub.mode
    baud = hub.baud
    scenario = getattr(hub.link, "scenario", "auto") if was == "sim" else "auto"
    speed = getattr(hub.link, "speed", 1.0) if was == "sim" else 1.0
    if not compile_only and was == "serial" and not port:
        port = hub.port
    build = os.path.join(os.environ.get("TEMP", BASE), "fog_build")  # short path: Windows limits long ones
    job.log(f"Compiling {os.path.basename(FW_DIR)} for {FQBN} ...")
    rc, out = _run_cmd(job, [cli, "compile", "--fqbn", FQBN, "--build-path", build, "--warnings", "default", FW_DIR])
    if rc != 0:
        raise RuntimeError("Compile failed (see the log). Nothing was uploaded.")
    sizes = re.findall(r"(Sketch uses \d+ bytes \(\d+%\)|Global variables use \d+ bytes \(\d+%\))", out)
    res = {"sizes": sizes}
    if compile_only:
        job.log("Compile OK (not uploaded).")
        return res
    if was == "sim":
        job.log("Simulator mode: restarting the simulator with the new calibration.h / model_weights.h ...")
        hub.connect("sim", scenario=scenario, speed=speed)
        res["applied"] = "simulator"
        return res
    if not port:
        raise RuntimeError("No COM port: choose the port in Settings (or connect first).")
    if was == "serial":
        job.log(f"Releasing {port} for the upload ...")
        hub.disconnect(quiet=True)
        time.sleep(1.0)
    job.log(f"Uploading to {port} ...")
    rc, _ = _run_cmd(job, [cli, "upload", "--fqbn", FQBN, "-p", port, "--build-path", build, FW_DIR])
    if rc != 0:
        if was == "serial":
            _reconnect(job, hub, port, baud)
        raise RuntimeError("Upload failed (see the log): check the port, close other programs using it, and try again.")
    job.log("Upload OK.")
    res["uploaded"] = port
    time.sleep(1.5)
    _reconnect(job, hub, port, baud)
    return res


def _reconnect(job, hub, port, baud):
    try:
        hub.connect("serial", port, baud)
        job.log(f"Dashboard reconnected to {port} (the board restarts: data in ~3 s).")
    except (OSError, RuntimeError, ValueError) as e:
        job.log(f"Could not reconnect to {port}: {e}")


def start_upload(hub, port="", compile_only=False):
    JOB.start("firmware", _upload, hub, port, compile_only)


# ----------------------------------------------------------------------------- learning
def datasets(kind="train"):
    """Recorded CSV files (train_*.csv, or sim_*.csv) with the number of rows per label."""
    files, totals, ltotals = [], {c: 0 for c in CLASSES}, {c: 0 for c in LIGHT_CLASSES}
    for p in sorted(glob.glob(os.path.join(DATA_DIR, f"{kind}_*.csv")), reverse=True):
        counts, lcounts, notes = {c: 0 for c in CLASSES}, {c: 0 for c in LIGHT_CLASSES}, set()
        try:
            with open(p, encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    if row.get("label") in counts:
                        counts[row["label"]] += 1
                        notes.add(row.get("note") or "-")
                    if row.get("light_cond") in lcounts:
                        lcounts[row["light_cond"]] += 1
        except OSError:
            continue
        for c in CLASSES:
            totals[c] += counts[c]
        for c in LIGHT_CLASSES:
            ltotals[c] += lcounts[c]
        files.append({"name": os.path.basename(p), "counts": counts, "light": lcounts, "notes": sorted(notes)})
    return {"kind": kind, "files": files, "totals": totals, "rows": sum(totals.values()),
            "ready": all(totals[c] >= 5 for c in CLASSES),
            "lightTotals": ltotals, "lightReady": all(ltotals[c] >= 5 for c in LIGHT_CLASSES)}


def model_info():
    info = {"exists": os.path.exists(MODEL_PATH), "trainedOn": "", "modified": 0}
    if info["exists"]:
        info["modified"] = os.path.getmtime(MODEL_PATH)
        with open(MODEL_PATH, encoding="utf-8") as fh:
            head = fh.read(600)
        m = re.search(r"// Trained on (.+)", head)
        info["trainedOn"] = m.group(1).strip() if m else "hand-tuned default weights"
    return info


def parse_report(text):
    """Pull the numbers out of train_model.py's printed report."""
    r = {"accuracy": None, "confusion": [], "recall": {}, "precision": {}, "influence": {}, "compare": [], "drop": [], "warning": "",
         "rows": None, "testRows": None}
    m = re.search(r"Total (\d+) rows", text)
    r["rows"] = int(m.group(1)) if m else None
    m = re.search(r"Test result on (\d+) unseen rows", text)
    r["testRows"] = int(m.group(1)) if m else None
    m = re.search(r"Accuracy:\s*([\d.]+)%", text)
    r["accuracy"] = float(m.group(1)) if m else None
    for cls in CLASSES:
        m = re.search(rf"^\s*{cls}\s+(\d+)\s+(\d+)\s+(\d+)\s*$", text, re.M)
        if m:
            r["confusion"].append([int(x) for x in m.groups()])
        m = re.search(rf"^\s*{cls}:\s*recall\s*([\d.]+)%\s*precision\s*([\d.]+)%", text, re.M)
        if m:
            r["recall"][cls], r["precision"][cls] = float(m.group(1)), float(m.group(2))
    for m in re.finditer(r"^\s{2}(temp|humidity|dew_spread|light|attenuation)\s+([\d.]+)\s*$", text, re.M):
        r["influence"][m.group(1)] = float(m.group(2))
    for m in re.finditer(r"^\s{2}(.+?)\s{2,}([\d.]+)%\s+([\d.]+)%\s+([\d.]+)%\s+([\d.]+)%\s*$", text, re.M):
        r["compare"].append({"name": m.group(1).strip(), "acc": float(m.group(2)), "clear": float(m.group(3)),
                             "light": float(m.group(4)), "dense": float(m.group(5))})
    for m in re.finditer(r"^\s{2}without (\w+)\s+([\d.]+)%\s+\(loss ([+-][\d.]+) points\)", text, re.M):
        r["drop"].append({"feature": m.group(1), "acc": float(m.group(2)), "loss": float(m.group(3))})
    m = re.search(r"WARNING: (.+)", text)
    r["warning"] = m.group(1).strip() if m else ""
    # light model (day / twilight / night): its own report follows the fog one
    head, sep, ltext = text.partition("Light model test result on ")
    if sep:
        lr = {"rows": None, "accuracy": None, "confusion": [], "recall": {}, "precision": {}}
        m = re.match(r"(\d+) unseen rows", ltext)
        lr["rows"] = int(m.group(1)) if m else None
        m = re.search(r"Accuracy:\s*([\d.]+)%", ltext)
        lr["accuracy"] = float(m.group(1)) if m else None
        for cls in LIGHT_CLASSES:
            m = re.search(rf"^\s*{cls}\s+(\d+)\s+(\d+)\s+(\d+)\s*$", ltext, re.M)
            if m:
                lr["confusion"].append([int(x) for x in m.groups()])
            m = re.search(rf"^\s*{cls}:\s*recall\s*([\d.]+)%\s*precision\s*([\d.]+)%", ltext, re.M)
            if m:
                lr["recall"][cls], lr["precision"][cls] = float(m.group(1)), float(m.group(2))
        r["light"] = lr
    return r


def _train(job, compare, kind, install):
    ds = datasets(kind)
    if not ds["ready"]:
        raise RuntimeError(f"Need at least 5 rows of every class in {kind}_*.csv (have {ds['totals']}). Record data first.")
    cmd = [sys.executable, TRAIN, "--data", os.path.join(DATA_DIR, f"{kind}_*.csv")]
    if compare:
        cmd.append("--compare")
    if install:
        if os.path.exists(MODEL_PATH):
            job.log("Backup of model_weights.h: " + os.path.basename(_backup(MODEL_PATH, "model_weights")))
        cmd.append("--install")
    rc, out = _run_cmd(job, cmd, cwd=BASE)
    if rc != 0:
        raise RuntimeError("train_model.py failed (see the log).")
    res = parse_report(out)
    res.update(kind=kind, installed=install, compare_run=compare, model=model_info())
    if install:
        job.log("model_weights.h written. Upload the firmware to put the new weights on the board.")
    return res


def start_training(compare=False, kind="train", install=False):
    if kind not in ("train", "sim"):
        raise ValueError("kind must be train or sim")
    if install and kind == "sim":
        raise ValueError("Simulated data must not be installed as the model.")
    JOB.start("install" if install else "train", _train, compare, kind, install)
