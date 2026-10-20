"""
Fog Illumination dashboard server (Python standard library only, no pip install).

  python dashboard/server.py --open                 start, open the browser, choose the port on the page
  python dashboard/server.py --port COM7 --open     connect to the Arduino Uno right away
  python dashboard/server.py --sim --open           run on the built-in simulator (no hardware)
  python dashboard/server.py --auto --open          connect if exactly one Arduino-like COM port is plugged in

The dashboard talks to the Uno over USB serial (115200 baud) with tools/serial_port.py:
it reads the "H," / "D," / "S," / "C," / "# ..." lines and sends the single-letter
commands and the "!get / !set key=value / !reset" lines (see README.id.md).
Close the Arduino Serial Monitor and tools/record_data.py first: only one program can own a COM port.
"""
import argparse
import base64
import collections
import csv
import json
import os
import queue
import re
import sqlite3
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(BASE, "tools"))
sys.path.insert(0, HERE)
import drivers  # noqa: E402
import maintenance  # noqa: E402
import serial_port  # noqa: E402
import simulator  # noqa: E402

STATIC = os.path.join(HERE, "static")
DATA_DIR = os.path.join(BASE, "data")  # same folder as tools/record_data.py (train_*.csv)
DB_DIR = os.path.join(HERE, "data")
DB_PATH = os.path.join(DB_DIR, "fog_log.db")
PHOTO_DIR = os.path.join(DB_DIR, "photos")
TEAM_LIMITS = {"author": (0, 3), "supervisor": (0, 2)}  # (min kept on delete, max)
PHOTO_MAX_BYTES = 2 * 1024 * 1024
HISTORY_MAX = 3600        # one sample per second = 1 hour
TX_GAP_S = 0.15           # pause between lines sent to the Uno (its serial buffer is only 64 bytes)
LABELS = ["clear", "light_fog", "dense_fog"]
LIGHT_LABELS = ["day", "twilight", "night"]
COMMANDS = {"calibrate": "c", "demo": "d", "page": "p", "info": "i", "help": "h"}
INT_FIELDS = {"light_raw", "pred", "state", "fault", "demo", "ms"}

# ---------------------------------------------------------------- project team (Author page)
def team_list():
    with HUB.lock:
        rows = [dict(zip(("id", "role", "name", "idno", "email", "photo", "sort"), r))
                for r in HUB.db.execute("SELECT id,role,name,idno,email,photo,sort FROM team ORDER BY sort, id")]
    for r in rows:
        r["photoUrl"] = f"/team-photo/{r['photo']}" if r.get("photo") else ""
    return {"authors": [r for r in rows if r["role"] == "author"],
            "supervisors": [r for r in rows if r["role"] == "supervisor"]}


def _photo_bytes(data_url):
    """data:image/...;base64,... -> (bytes, ext) after checking the file really is an image."""
    m = re.match(r"data:image/(jpeg|jpg|png|webp);base64,(.+)$", data_url or "", re.S)
    if not m:
        return None, "the photo must be a JPEG, PNG or WebP image"
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except ValueError:
        return None, "the photo could not be read"
    if len(raw) > PHOTO_MAX_BYTES:
        return None, "the photo is larger than 2 MB"
    if raw[:3] == b"\xff\xd8\xff":
        return raw, "jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return raw, "png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return raw, "webp"
    return None, "the file is not a JPEG, PNG or WebP image"


def _drop_photo(name):
    if name:
        try:
            os.remove(os.path.join(PHOTO_DIR, name))
        except OSError:
            pass


def _team_one(pid):
    with HUB.lock:
        r = HUB.db.execute("SELECT id,role,name,idno,email,photo FROM team WHERE id=?", (pid,)).fetchone()
    return dict(zip(("id", "role", "name", "idno", "email", "photo"), r)) if r else None


def _team_count(role):
    with HUB.lock:
        return HUB.db.execute("SELECT COUNT(*) FROM team WHERE role=?", (role,)).fetchone()[0]


def _team_exec(sql, args=()):
    with HUB.lock:
        cur = HUB.db.execute(sql, args)
        HUB.db.commit()
        return cur.lastrowid


def team_save(data):
    role = data.get("role")
    if role not in TEAM_LIMITS:
        return {"error": "role must be author or supervisor"}, 400
    name = re.sub(r"\s+", " ", str(data.get("name") or "")).strip()[:60]
    idno = str(data.get("idno") or "").strip()[:30]
    email = str(data.get("email") or "").strip()[:80]
    if not name:
        return {"error": "the name is required"}, 400
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return {"error": "that e-mail address does not look right"}, 400
    pid = data.get("id")
    old = _team_one(pid) if pid else None
    if pid and not old:
        return {"error": "that person no longer exists"}, 404
    if (not old or old["role"] != role) and _team_count(role) >= TEAM_LIMITS[role][1]:
        return {"error": f"at most {TEAM_LIMITS[role][1]} {role}s"}, 409
    photo = old["photo"] if old else ""
    new_photo = None
    if data.get("photo"):
        raw, ext = _photo_bytes(data["photo"])
        if raw is None:
            return {"error": ext}, 400
        new_photo = (raw, ext)
    if old:
        _team_exec("UPDATE team SET role=?, name=?, idno=?, email=? WHERE id=?", (role, name, idno, email, pid))
    else:
        with HUB.lock:
            sort = HUB.db.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM team").fetchone()[0]
        pid = _team_exec("INSERT INTO team(role, name, idno, email, photo, sort) VALUES (?,?,?,?,?,?)",
                         (role, name, idno, email, "", sort))
    if new_photo or data.get("removePhoto"):
        _drop_photo(photo)
        photo = ""
        if new_photo:
            os.makedirs(PHOTO_DIR, exist_ok=True)
            photo = f"{int(pid)}-{int(time.time())}.{new_photo[1]}"   # new name = no stale browser cache
            with open(os.path.join(PHOTO_DIR, photo), "wb") as fh:
                fh.write(new_photo[0])
        _team_exec("UPDATE team SET photo=? WHERE id=?", (photo, pid))
    return {"ok": True, "id": pid, **team_list()}, 200


def team_delete(data):
    p = _team_one(data.get("id"))
    if not p:
        return {"error": "that person no longer exists"}, 404
    _team_exec("DELETE FROM team WHERE id=?", (p["id"],))
    _drop_photo(p["photo"])
    return {"ok": True, **team_list()}, 200



ARDUINO_DRIVERS = ("USBSER", "CH341", "SILABSER")


def serial_ports():
    """COM ports; the registry calls a CH340 board "Serial2", so the chip name comes from Windows PnP (VID)."""
    ports = [{"port": a, "driver": b, "arduino": b.upper().startswith(ARDUINO_DRIVERS)} for a, b in serial_port.list_ports()]
    if ports:
        by_com = {d["com"]: d for d in drivers.detect()["devices"] if d["com"]}
        for p in ports:
            d = by_com.get(p["port"])
            if d:
                p["driver"], p["arduino"] = d["chip"], True
    return ports


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


class Hub:
    """Owns the link to the Uno (real serial port or simulator), the latest data and the SSE subscribers."""

    def __init__(self):
        self.lock = threading.RLock()
        self.link = None
        self.mode = None            # "serial" | "sim" | None
        self.port = ""
        self.baud = 115200
        self.link_since = 0.0
        self.link_error = ""
        self.pending_get = 0.0
        self.header = []
        self.sample = None
        self.status = None          # last S line
        self.raw = None             # last R line (unfiltered sensor values)
        self.cfg = {}
        self.cal_board = {}      # calibration compiled into the board (K line)
        self.fw = ""
        self.last_d = 0.0
        self.last_line = 0.0
        self.faults_prev = None
        self.status_prev = None
        self.history = collections.deque(maxlen=HISTORY_MAX)
        self.console = collections.deque(maxlen=400)
        self.events = collections.deque(maxlen=500)
        self.log_seq = 0
        self.console_seq = 0
        self.tx = collections.deque()
        self.tx_next = 0.0
        self.subs = []
        self.dirty = False
        self.rec = {"on": False, "label": "", "light": "", "note": "", "path": "", "fh": None, "writer": None,
                    "counts": {k: 0 for k in LABELS}, "skipped": 0}
        self._init_db()
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        for ts, kind, text in self._db_rows("SELECT ts,kind,text FROM events ORDER BY id DESC LIMIT 200"):
            self.events.appendleft({"ts": ts, "kind": kind, "text": text})

    # ----------------------------------------------------------- persistence
    def _init_db(self):
        os.makedirs(DB_DIR, exist_ok=True)
        c = sqlite3.connect(DB_PATH)
        c.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, text TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS team(id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, name TEXT, idno TEXT, email TEXT, photo TEXT, sort INTEGER)")
        c.commit()
        c.close()

    def _db_rows(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def log(self, kind, text):
        ev = {"ts": now_iso(), "kind": kind, "text": text}
        with self.lock:
            self.events.append(ev)
            self.log_seq += 1
            self.db.execute("INSERT INTO events(ts,kind,text) VALUES(?,?,?)", (ev["ts"], kind, text))
            self.db.commit()
            self.dirty = True

    # ----------------------------------------------------------- connection
    def connect(self, mode, port="", baud=115200, scenario="auto", speed=1.0):
        self.disconnect(quiet=True)
        try:
            if mode == "sim":
                link = simulator.FogSim(scenario, speed)
                port = "simulator"
            elif mode == "serial":
                if not port:
                    raise OSError("No COM port selected.")
                link = serial_port.LineReader(port, baud)
            else:
                raise ValueError("mode must be 'serial' or 'sim'")
        except (OSError, RuntimeError, ValueError) as e:
            with self.lock:
                self.link_error = str(e)
                self.dirty = True
            self.log("link", f"Connect failed: {e}")
            raise
        with self.lock:
            self.link, self.mode, self.port, self.baud = link, mode, port, baud
            self.link_since, self.link_error = time.time(), ""
            self.header, self.sample, self.status, self.cfg, self.fw = [], None, None, {}, ""
            self.raw = None
            self.cal_board = {}
            self.faults_prev = self.status_prev = None
            self.last_d = self.last_line = 0.0
            self.tx.clear()
            self.pending_get = time.time() + (0.2 if mode == "sim" else 3.0)  # a real Uno restarts on open
            self.dirty = True
        self.log("link", f"Connected to {port}" + (f" at {baud} baud" if mode == "serial" else f" (scenario {scenario})"))

    def disconnect(self, quiet=False, error=""):
        with self.lock:
            link, self.link = self.link, None
            had = self.mode
            self.mode, self.link_error = None, error
            self.stop_recording()
            self.dirty = True
        if link:
            try:
                link.close()
            except OSError:
                pass
        if had and not quiet:
            self.log("link", "Disconnected" + (f": {error}" if error else ""))

    def queue_tx(self, text):
        with self.lock:
            if not self.link:
                raise ConnectionError("Not connected to the Arduino.")
            self.tx.append(text)

    # ----------------------------------------------------------- reader loop
    def run(self):
        while True:
            with self.lock:
                link = self.link
            if link:
                try:
                    for line in link.lines():
                        self.handle_line(line)
                    self.pump_tx(link)
                except OSError as e:
                    self.disconnect(error=str(e))
            if self.dirty:
                self.dirty = False
                self.broadcast()
            time.sleep(0.05)

    def pump_tx(self, link):
        t = time.time()
        with self.lock:
            if self.pending_get and t >= self.pending_get:
                self.pending_get = 0.0
                self.tx.append("!get\n")
            if self.tx and t >= self.tx_next:
                text = self.tx.popleft()
                self.tx_next = t + TX_GAP_S
            else:
                return
        link.send(text)

    def handle_line(self, line):
        if not line:
            return
        self.last_line = time.time()
        if line.startswith("D,"):
            self.on_sample(line)
        elif line.startswith("H,"):
            self.header = line.split(",")[1:]
        elif line.startswith("S,"):
            self.on_status(line)
        elif line.startswith("R,"):
            self.on_raw(line)
        elif line.startswith("C,"):
            cfg = {}
            for kv in line[2:].split(","):
                k, _, v = kv.partition("=")
                if k in simulator.CFG_LIMITS and v.lstrip("-").isdigit():
                    cfg[k] = int(v)
            if cfg:
                with self.lock:
                    changed = cfg != self.cfg
                    self.cfg = cfg
                    self.dirty = True
                if changed:
                    self.log("settings", "Device settings: " + " ".join(f"{k}={v}" for k, v in cfg.items()))
        elif line.startswith("K,"):
            cal = {}
            for kv in line[2:].split(","):
                k, _, v = kv.partition("=")
                try:
                    cal[k] = float(v)
                except ValueError:
                    pass
            if cal:
                with self.lock:
                    self.cal_board = cal
                    self.dirty = True
        elif line.startswith("#"):
            with self.lock:
                self.console.append({"ts": now_iso(), "text": line})
                self.console_seq += 1
                m = re.match(r"# Fog Illumination Uno v(\S+)", line)
                if m:
                    self.fw = m.group(1)
                self.dirty = True
            if line.startswith("# STATE"):
                self.log("state", line[2:])
            elif line.startswith("# CAL"):
                self.log("calibration", line[2:])
            elif line.startswith("# DEMO"):
                self.log("demo", line[2:])
            elif line.startswith("# ERR"):
                self.log("error", line[2:])

    def on_sample(self, line):
        raw = line.split(",")[1:]
        if not self.header or len(raw) != len(self.header):
            return
        row = {}
        for k, v in zip(self.header, raw):
            try:
                row[k] = int(float(v)) if k in INT_FIELDS else float(v)
            except ValueError:
                row[k] = None
        row["ts"] = time.time()
        with self.lock:
            self.sample = row
            self.last_d = row["ts"]
            self.dirty = True
            faults = row.get("fault", 0)
            if self.faults_prev is not None and faults != self.faults_prev:
                names = [n for bit, n in ((1, "DHT11"), (2, "Laser")) if faults & bit]
                self.log("fault", ("Fault: " + " + ".join(names)) if names else "Faults cleared")
            self.faults_prev = faults
            rec = self.rec
            if rec["on"] and rec["label"]:
                if row.get("fault") or row.get("demo"):
                    rec["skipped"] += 1
                else:
                    self.write_row(raw)
        if self.status is None:  # board without S lines (older firmware): chart what we have
            self.add_history()

    def on_raw(self, line):
        p = line.split(",")[1:]
        if len(p) < 6:
            return
        try:
            raw = {"ms": int(p[0]), "temp": float(p[1]), "hum": float(p[2]),
                   "ldr": int(p[3]), "rxOff": int(p[4]), "rxOn": int(p[5])}
        except ValueError:
            return
        with self.lock:
            self.raw = raw
            self.dirty = True

    def on_status(self, line):
        p = line.split(",")[1:]
        if len(p) < 4:
            return
        try:
            st = {"mode": int(p[0]), "calibrated": int(p[1]) == 1,
                  "dhtFails": int(p[2]), "rxSat": int(p[3]) == 1}
            if len(p) >= 8:  # light condition: class 0 day, 1 twilight, 2 night + probabilities
                st["light"] = int(p[4])
                st["lightP"] = [float(x) for x in p[5:8]]
        except ValueError:
            return
        with self.lock:
            prev, self.status = self.status_prev, st
            self.status_prev = st
            if prev:
                if st["calibrated"] != prev["calibrated"]:
                    self.log("calibration", "Laser calibrated" if st["calibrated"] else "Calibration lost")
                if st["rxSat"] != prev["rxSat"]:
                    self.log("fault", "Receiver saturated" if st["rxSat"] else "Receiver no longer saturated")
                if (st["dhtFails"] > 0) != (prev["dhtFails"] > 0):
                    self.log("fault", "DHT11 read failing" if st["dhtFails"] else "DHT11 reading again")
            self.dirty = True
        self.add_history()

    def add_history(self):
        s = self.sample
        if not s:
            return
        st = self.status or {}
        pt = {"t": round(s["ts"], 1), "temp": s.get("temp"), "hum": s.get("hum"), "spread": s.get("dew_spread"),
              "light": s.get("f_light"), "sig": s.get("laser_sig"), "att": s.get("f_attenuation"),
              "p": [s.get("p_clear"), s.get("p_light"), s.get("p_dense")], "state": s.get("state"),
              "mode": st.get("mode", s.get("state")), "fault": s.get("fault")}
        with self.lock:
            self.history.append(pt)

    # ----------------------------------------------------------- recording (same CSV as tools/record_data.py)
    def start_recording(self, label, note, light=""):
        if label not in LABELS:
            raise ValueError("label must be one of " + ", ".join(LABELS))
        if light not in [""] + LIGHT_LABELS:
            raise ValueError("light must be empty or one of " + ", ".join(LIGHT_LABELS))
        with self.lock:
            if not self.link:
                raise ConnectionError("Not connected to the Arduino.")
            r = self.rec
            if r["on"] and (r["label"] != label or r["light"] != light):  # new label: keep the file, just switch
                r["label"], r["light"] = label, light
            elif not r["on"]:
                r.update(on=True, path="", fh=None, writer=None, counts={k: 0 for k in LABELS}, skipped=0, label=label, light=light)
            r["note"] = (note or "").replace(",", " ").strip()
            self.dirty = True
        self.log("record", f"Recording: label={label} light={light or '-'} note={self.rec['note'] or '-'}")

    def write_row(self, raw):
        r = self.rec
        if r["writer"] is None:
            os.makedirs(DATA_DIR, exist_ok=True)
            # simulator data must never end up in train_*.csv (train_model.py would learn from it)
            prefix = "sim_" if self.mode == "sim" else "train_"
            r["path"] = os.path.join(DATA_DIR, datetime.now().strftime(prefix + "%Y%m%d_%H%M%S.csv"))
            r["fh"] = open(r["path"], "w", newline="", encoding="utf-8")
            r["writer"] = csv.writer(r["fh"])
            r["writer"].writerow(["pc_time", "label", "note", "light_cond"] + self.header)
        r["writer"].writerow([now_iso(), r["label"], r["note"], r["light"]] + raw)
        r["fh"].flush()
        r["counts"][r["label"]] += 1

    def stop_recording(self):
        with self.lock:
            r = self.rec
            was = r["on"]
            if r["fh"]:
                r["fh"].close()
            total = sum(r["counts"].values())
            path = r["path"]
            r.update(on=False, label="", light="", fh=None, writer=None)
            self.dirty = True
        if was:
            self.log("record", f"Recording stopped: {total} rows" + (f" -> {os.path.basename(path)}" if path else ""))

    def recordings(self):
        out = []
        if os.path.isdir(DATA_DIR):
            for n in sorted(os.listdir(DATA_DIR), reverse=True):
                if re.match(r"^(train|sim)_\d{8}_\d{6}\.csv$", n):
                    p = os.path.join(DATA_DIR, n)
                    with open(p, encoding="utf-8") as fh:
                        rows = max(0, sum(1 for _ in fh) - 1)
                    out.append({"name": n, "rows": rows, "bytes": os.path.getsize(p), "sim": n.startswith("sim_")})
        return out[:30]

    # ----------------------------------------------------------- snapshot / SSE
    def snapshot(self):
        with self.lock:
            r = self.rec
            sim = self.link if self.mode == "sim" else None
            return _clean({
                "link": {"mode": self.mode, "port": self.port, "baud": self.baud, "error": self.link_error,
                         "since": self.link_since, "fw": self.fw,
                         "dataAge": (time.time() - self.last_d) if self.last_d else None,
                         "hasStatus": self.status is not None},
                "sample": self.sample, "status": self.status, "raw": self.raw, "cfg": self.cfg, "calBoard": self.cal_board,
                "limits": simulator.CFG_LIMITS,
                "scenario": sim.scenario if sim else None, "scenarios": simulator.SCENARIOS,
                "recording": {"on": r["on"], "label": r["label"], "light": r["light"], "note": r["note"],
                              "file": os.path.basename(r["path"]) if r["path"] else "",
                              "counts": r["counts"], "skipped": r["skipped"]},
                "logSeq": self.log_seq, "consoleSeq": self.console_seq, "now": time.time(),
            })

    def broadcast(self):
        msg = json.dumps(self.snapshot())
        for q in list(self.subs):
            try:
                q.put_nowait(msg)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except (queue.Empty, queue.Full):
                    pass


def _clean(o):
    """NaN is not valid JSON: send null instead."""
    if isinstance(o, float):
        return None if o != o or o in (float("inf"), float("-inf")) else o
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


HUB = None


def config_pins():
    """PIN_* defines from config.h with their comments (for the wiring card)."""
    out = []
    with open(os.path.join(BASE, "firmware", "fog_illumination_uno", "config.h"), encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"#define\s+(PIN_\w+)\s+(\w+)\s*(?://\s*(.*))?", line)
            if m:
                out.append({"name": m.group(1), "pin": m.group(2), "note": (m.group(3) or "").strip()})
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "FogDashboard/1.0"

    def log_message(self, fmt, *args):  # quiet
        pass

    # ---- helpers
    def _json(self, obj, code=200):
        body = json.dumps(_clean(obj)).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype, download=None):
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            return self._json({"error": "not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(body)

    def _body(self, limit=100_000):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def _local_only(self):
        """The page can drive the board and start installers: refuse other Host names (DNS rebinding)
        and cross-site requests (a web page in this browser posting to localhost)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        origin = self.headers.get("Origin")
        ok_origin = origin is None or urlparse(origin).hostname in ("localhost", "127.0.0.1", "::1")
        if host not in ("localhost", "127.0.0.1", "::1") or not ok_origin:
            self._json({"error": "forbidden: local access only"}, 403)
            return False
        return True

    # ---- GET
    def do_GET(self):
        if not self._local_only():
            return
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        if p in ("/", "/index.html"):
            return self._file(os.path.join(STATIC, "index.html"), "text/html; charset=utf-8")
        if p == "/api/state":
            return self._json(HUB.snapshot())
        if p == "/api/team":
            return self._json({**team_list(), "editable": True, "limits": TEAM_LIMITS})
        if p.startswith("/team-photo/"):
            name = p.rsplit("/", 1)[-1]
            if not re.fullmatch(r"\d+-\d+\.(jpg|png|webp)", name):
                return self._json({"error": "not found"}, 404)
            ctype = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[name.rsplit(".", 1)[-1]]
            return self._file(os.path.join(PHOTO_DIR, name), ctype)
        if p == "/api/stream":
            return self._stream()
        if p == "/api/history":
            n = min(int(q.get("n", ["600"])[0] or 600), HISTORY_MAX)
            with HUB.lock:
                return self._json(list(HUB.history)[-n:])
        if p == "/api/logs":
            n = min(int(q.get("n", ["200"])[0] or 200), 500)
            with HUB.lock:
                return self._json(list(HUB.events)[-n:][::-1])
        if p == "/api/logs.csv":
            rows = HUB._db_rows("SELECT ts,kind,text FROM events ORDER BY id")
            body = "ts,kind,text\n" + "\n".join(",".join('"' + str(c).replace('"', '""') + '"' for c in r) for r in rows)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="fog_events.csv"')
            self.end_headers()
            return self.wfile.write(body.encode("utf-8"))
        if p == "/api/console":
            with HUB.lock:
                return self._json(list(HUB.console)[-200:][::-1])
        if p == "/api/ports":
            return self._json(serial_ports())
        if p == "/api/calibration":
            return self._json({"file": maintenance.read_calibration(), "fields": {
                k: {"min": v[1], "max": v[2], "default": v[3]} for k, v in maintenance.CAL_FIELDS.items()},
                "text": maintenance.calibration_text(maintenance.read_calibration())})
        if p == "/api/job":
            return self._json(maintenance.JOB.snapshot())
        if p == "/api/learning":
            kind = q.get("kind", ["train"])[0]
            return self._json({"data": maintenance.datasets("sim" if kind == "sim" else "train"),
                               "model": maintenance.model_info(), "cli": bool(maintenance.find_cli())})
        if p == "/api/pins":
            return self._json(config_pins())
        if p.startswith("/docs/"):
            name = p[len("/docs/"):]
            if re.match(r"^[\w-]+\.(svg|png)$", name):
                return self._file(os.path.join(BASE, "docs", name), "image/svg+xml" if name.endswith(".svg") else "image/png")
        if p == "/api/drivers":
            res = drivers.detect()
            try:
                res["local"], res["folder"] = drivers.local_items(), drivers.DRIVERS_DIR
            except OSError as e:
                res["local"], res["folder"], res["error"] = [], drivers.DRIVERS_DIR, res["error"] or str(e)
            return self._json(res)
        if p == "/api/recordings":
            return self._json({"files": HUB.recordings(), "folder": DATA_DIR})
        if p.startswith("/data/"):
            name = p[len("/data/"):]
            if re.match(r"^(train|sim)_\d{8}_\d{6}\.csv$", name):
                return self._file(os.path.join(DATA_DIR, name), "text/csv; charset=utf-8", download=name)
        return self._json({"error": "not found"}, 404)

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = queue.Queue(maxsize=20)
        HUB.subs.append(q)
        try:
            self.wfile.write(("data: " + json.dumps(HUB.snapshot()) + "\n\n").encode())
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(("data: " + msg + "\n\n").encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (OSError, ValueError):
            pass
        finally:
            if q in HUB.subs:
                HUB.subs.remove(q)

    # ---- POST
    def do_POST(self):
        if not self._local_only():
            return
        p = urlparse(self.path).path
        d = self._body(600_000 if p == "/api/team/save" else 100_000)  # a team photo is sent as a data: URL
        try:
            if p in ("/api/team/save", "/api/team/delete"):
                res, code = team_save(d) if p == "/api/team/save" else team_delete(d)
                return self._json(res, code)
            if p == "/api/connect":
                HUB.connect(d.get("mode", "serial"), d.get("port", ""), int(d.get("baud") or 115200),
                            d.get("scenario", "auto"), float(d.get("speed") or 1.0))
            elif p == "/api/disconnect":
                HUB.disconnect()
            elif p == "/api/command":
                cmd = d.get("cmd")
                if cmd not in COMMANDS:
                    return self._json({"error": "unknown command"}, 400)
                if cmd == "demo" and "value" in d:  # absolute on/off: the board only has a toggle
                    cur = bool((HUB.sample or {}).get("demo"))
                    if cur == bool(d["value"]):
                        return self._json({"ok": True, "note": "already so"})
                HUB.queue_tx(COMMANDS[cmd])
                HUB.log("command", f"Sent: {cmd}")
            elif p == "/api/io":  # Test IO page: drive one output by hand (firmware "!io ...")
                if "led" in d:
                    line = f"led={max(0, min(255, int(d['led'])))}"
                elif "status" in d:
                    line = f"status={1 if int(d['status']) else 0}"
                elif d.get("laser"):
                    line = "laser=1"
                elif d.get("lcd"):
                    line = "lcd=1"
                elif d.get("off"):
                    line = "off"
                else:
                    return self._json({"error": "unknown io request"}, 400)
                HUB.queue_tx(f"!io {line}\n")
            elif p == "/api/settings":
                if d.get("reset"):
                    HUB.queue_tx("!reset\n")
                    HUB.log("settings", "Sent: restore default settings")
                elif d.get("read"):
                    HUB.queue_tx("!get\n")
                else:
                    sent = []
                    for k, v in d.items():
                        if k not in simulator.CFG_LIMITS:
                            return self._json({"error": f"unknown setting {k}"}, 400)
                        lo, hi = simulator.CFG_LIMITS[k]
                        n = max(lo, min(hi, int(float(v))))
                        HUB.queue_tx(f"!set {k}={n}\n")
                        sent.append(f"{k}={n}")
                    HUB.queue_tx("!get\n")
                    HUB.log("settings", "Sent: " + " ".join(sent))
            elif p == "/api/scenario":
                with HUB.lock:
                    if HUB.mode != "sim":
                        return self._json({"error": "scenarios exist only in simulator mode"}, 409)
                    HUB.link.set_scenario(d.get("name", ""))
                HUB.log("simulator", f"Scenario: {d.get('name')}")
                HUB.dirty = True
            elif p == "/api/calibration/save":
                vals, text, backup = maintenance.write_calibration(d)
                HUB.log("calibration", "calibration.h written: " + " ".join(f"{k}={v}" for k, v in vals.items()))
                return self._json({"ok": True, "file": vals, "text": text, "backup": os.path.basename(backup)})
            elif p == "/api/firmware/upload":
                maintenance.start_upload(HUB, str(d.get("port", "")), bool(d.get("compileOnly")))
                HUB.log("firmware", "Compile" + ("" if d.get("compileOnly") else " + upload") + " started from the dashboard")
            elif p == "/api/learning/train":
                maintenance.start_training(bool(d.get("compare")), "sim" if d.get("kind") == "sim" else "train", False)
                HUB.log("learning", "Training started (" + ("sim" if d.get("kind") == "sim" else "recorded") + " data)")
            elif p == "/api/learning/install":
                maintenance.start_training(False, "train", True)
                HUB.log("learning", "Training + model_weights.h install started")
            elif p == "/api/drivers/rescan":
                return self._json({"ok": True, "output": drivers.rescan()})
            elif p == "/api/drivers/install":
                msg = drivers.install(str(d.get("name", "")))
                HUB.log("driver", f"Started driver install: {d.get('name')}")
                return self._json({"ok": True, "message": msg})
            elif p == "/api/record":
                if d.get("action") == "start":
                    HUB.start_recording(d.get("label", ""), d.get("note", ""), d.get("light", ""))
                else:
                    HUB.stop_recording()
            else:
                return self._json({"error": "not found"}, 404)
        except ConnectionError as e:
            return self._json({"error": str(e)}, 409)
        except (OSError, RuntimeError, ValueError) as e:
            return self._json({"error": str(e)}, 400)
        HUB.dirty = True
        return self._json({"ok": True})


def main():
    global HUB
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http-port", type=int, default=5200)
    ap.add_argument("--host", default="127.0.0.1", help="keep 127.0.0.1: the page can drive the board")
    ap.add_argument("--port", help="COM port to connect to, e.g. COM7")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--sim", action="store_true", help="start on the simulator")
    ap.add_argument("--scenario", default="auto", choices=list(simulator.SCENARIOS))
    ap.add_argument("--speed", type=float, default=1.0, help="simulator speed (virtual seconds per real second)")
    ap.add_argument("--auto", action="store_true", help="connect if exactly one Arduino-like COM port exists")
    ap.add_argument("--open", action="store_true", help="open the dashboard in the browser")
    args = ap.parse_args()

    HUB = Hub()
    threading.Thread(target=HUB.run, daemon=True).start()
    try:
        if args.sim:
            HUB.connect("sim", scenario=args.scenario, speed=args.speed)
        elif args.port:
            HUB.connect("serial", args.port, args.baud)
        elif args.auto:
            cand = [p["port"] for p in serial_ports() if p["arduino"]]
            if len(cand) == 1:
                HUB.connect("serial", cand[0], args.baud)
            else:
                print(f"--auto: {len(cand)} Arduino-like ports found, choose one on the page.")
    except (OSError, RuntimeError, ValueError) as e:
        print(f"Could not connect: {e}  (choose the port on the page)")

    try:
        httpd = ThreadingHTTPServer((args.host, args.http_port), Handler)
    except OSError as e:
        sys.exit(f"Cannot listen on port {args.http_port}: {e}. Is the dashboard already running?")
    httpd.daemon_threads = True
    url = f"http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.http_port}/"
    print(f"Fog dashboard on {url}   (Ctrl+C to stop)")
    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        HUB.disconnect(quiet=True)


if __name__ == "__main__":
    main()
