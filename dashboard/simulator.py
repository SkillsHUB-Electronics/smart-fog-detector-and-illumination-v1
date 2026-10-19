"""
Software twin of the Arduino Uno firmware (firmware/fog_illumination_uno), so the
dashboard runs without hardware.

It speaks the SAME serial protocol as the board: it consumes the same commands
(c d p i h and the "!get / !set key=value / !reset" lines) and emits the same
lines (H, D, S, C and "# ..." messages). The dashboard cannot tell it apart
from a real Uno.

Behaviour is a line-by-line port of the firmware functions (same names in
snake_case). Constants come from firmware/.../config.h and the model weights
from model_weights.h, so editing those files changes the simulator too.
If you change the decision logic in the .ino, change it here as well.

Run it alone:  python dashboard/simulator.py [--scenario auto] [--speed 5]
"""
import argparse
import json
import math
import os
import random
import re
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW_DIR = os.path.join(BASE, "firmware", "fog_illumination_uno")
EEPROM_FILE = os.path.join(BASE, "dashboard", "data", "sim_eeprom.json")

STATE_NAME = ["CLEAR", "LIGHT FOG", "DENSE FOG", "FAULT"]
ST_CLEAR, ST_LIGHT, ST_DENSE, MODE_FAULT = 0, 1, 2, 3
FAULT_DHT, FAULT_LASER = 1, 2

# Runtime settings: key -> (min, max). Same limits as setCfg() in the firmware.
CFG_LIMITS = {
    "brightLight": (0, 255), "brightDense": (0, 255), "nightPct": (0, 100), "nightScale": (0, 100),
    "confirmUp": (1, 20), "confirmDown": (1, 20), "stepLight": (50, 1000), "stepDense": (50, 2000),
}

# Scenarios: what the "world" looks like. fog = target fog density 0..1.
SCENARIOS = {
    "auto": "Cycle: clear -> light fog -> dense fog -> clear (20 s each)",
    "clear": "Clear dry air",
    "light": "Light fog",
    "dense": "Dense fog",
    "humid_nofog": "Humid air without fog (92 % RH, laser clear)",
    "night_clear": "Clear night",
    "night_dense": "Dense fog at night",
    "dht_fail": "DHT11 not answering, clear air",
    "dht_fail_dense": "DHT11 not answering in dense fog",
    "laser_blocked": "Laser beam blocked in dry air",
    "rx_saturated": "Receiver saturated by strong light",
}


def _read_defines():
    """#define NAME number from config.h and calibration.h (a trailing 'f' as in 1.0f is ignored)."""
    d = {}
    for name in ("config.h", "calibration.h"):
        with open(os.path.join(FW_DIR, name), encoding="utf-8") as fh:
            for m in re.finditer(r"^#define\s+(\w+)\s+(-?[\d.]+)", fh.read(), re.M):
                d[m.group(1)] = float(m.group(2)) if "." in m.group(2) else int(m.group(2))
    return d


def _read_model():
    with open(os.path.join(FW_DIR, "model_weights.h"), encoding="utf-8") as fh:
        txt = fh.read()
    w_block = re.search(r"MODEL_W\[[^\]]*\]\[[^\]]*\]\s*=\s*\{(.*?)\};", txt, re.S).group(1)
    W = [[float(x) for x in re.findall(r"-?[\d.]+(?=f)", row)] for row in re.findall(r"\{([^{}]*)\}", w_block)]
    B = [float(x) for x in re.findall(r"-?[\d.]+(?=f)", re.search(r"MODEL_B\[[^\]]*\]\s*=\s*\{(.*?)\};", txt, re.S).group(1))]
    return W, B


def _fw_version():
    with open(os.path.join(FW_DIR, "fog_illumination_uno.ino"), encoding="utf-8") as fh:
        m = re.search(r'#define\s+FW_VERSION\s+"([^"]+)"', fh.read())
    return m.group(1) if m else "?"


def clampf(x, lo, hi):
    return lo if x < lo else (hi if x > hi else x)


def dew_point(t, rh):
    rh = clampf(rh, 1, 100)
    b, c = 17.62, 243.12
    g = math.log(rh / 100.0) + b * t / (c + t)
    return c * g / (b - g)


def fmt(v, n):
    """Serial.print(float, n)"""
    return "nan" if v != v else f"{v:.{n}f}"


class FogSim:
    HEADER = ("H,ms,temp,hum,dew_spread,light_raw,laser_sig,baseline,"
              "f_temp,f_humidity,f_dew_spread,f_light,f_attenuation,"
              "p_clear,p_light,p_dense,pred,state,fault,demo")

    def __init__(self, scenario="auto", speed=1.0, persist=True, seed=None):
        self.cfgh = _read_defines()
        self.W, self.B = _read_model()
        self.fw = _fw_version()
        self.rnd = random.Random(seed)
        self.speed = float(speed)
        self.persist = persist
        self.scenario = scenario if scenario in SCENARIOS else "auto"
        self.out = []
        self.rx = ""           # incoming bytes not yet parsed (firmware reads char by char)
        self.in_line, self.line = False, ""
        self.fog = 0.0         # current fog density of the world
        self.world_t = 0.0     # virtual seconds, drives the "auto" scenario
        # ---- firmware globals ----
        self.temp_c = self.hum_rh = self.dew_spread = float("nan")
        self.dht_fails = 0
        self.light_raw = 0
        self.laser_sig = 0.0
        self.baseline = 0.0
        self.calibrated = False
        self.rx_sat = False
        self.feat = [0.0] * 5
        self.prob = [0.0] * 3
        self.pred = self.state = self.pending = ST_CLEAR
        self.pending_count = 0
        self.faults = 0
        self.laser_bad_count = 0
        self.demo = False
        self.demo_mode = 0
        self.demo_since = 0
        self.last_dht = 0
        self.first_dht = True
        self.started = False
        self.last_sample = 0
        self.now = 0           # virtual millis()
        self._last_real = time.monotonic()
        self._acc = 0.0
        self.cfg = {}
        self.cfg_defaults()
        self._load_eeprom()
        # boot messages, like setup()
        self._boot()

    # ------------------------------------------------------------- link API
    def lines(self):
        """Advance the virtual clock to match real time and return the lines produced since the last call."""
        t = time.monotonic()
        self._acc += (t - self._last_real) * 1000.0 * self.speed
        self._last_real = t
        while self._acc >= 50:
            self._acc -= 50
            self.now += 50
            self._world_step(0.05)
            self._loop()
        out, self.out = self.out, []
        return out

    def send(self, text):
        self.rx += text

    def close(self):
        pass

    def set_scenario(self, name):
        if name in SCENARIOS:
            self.scenario = name

    # ------------------------------------------------------------- world model
    def _target(self):
        s = self.scenario
        if s == "auto":
            ph = int(self.world_t // 20) % 3
            return (0.0, 0.4, 0.9)[ph]
        return {"light": 0.4, "dense": 0.9, "night_dense": 0.9, "dht_fail_dense": 0.9}.get(s, 0.0)

    def _world_step(self, dt):
        self.world_t += dt * self.speed
        self.fog += (self._target() - self.fog) * min(1.0, 0.25 * dt * self.speed)

    def _ambient(self):
        base = 0.05 if self.scenario in ("night_clear", "night_dense") else 0.85
        if self.scenario == "rx_saturated":
            base = 1.2
        return base * (1.0 - 0.4 * self.fog)

    def _attenuation(self):
        return 1.0 if self.scenario == "laser_blocked" else clampf(1.05 * self.fog, 0.0, 1.0)

    def _adc_pair(self):
        """One laser OFF / ON reading pair: (off, on) after readAvg(8)."""
        n = lambda: self.rnd.gauss(0, 1.5)
        off = 60 + 500 * self._ambient() + n()
        on = off + 400.0 * (1.0 - self._attenuation()) + n()
        return int(clampf(round(off), 0, 1023)), int(clampf(round(on), 0, 1023))

    def _read_dht11(self):
        if self.scenario in ("dht_fail", "dht_fail_dense"):
            return None
        t = 25 - 3 * self.fog + self.rnd.gauss(0, 0.2)
        h = 92.0 if self.scenario == "humid_nofog" else 45 + 53 * self.fog + self.rnd.gauss(0, 1.0)
        return float(round(t)), float(round(clampf(h, 1, 100)))

    # ------------------------------------------------------------- firmware port
    def cfg_defaults(self):
        c = self.cfgh
        self.cfg = {
            "brightLight": c["BRIGHT_LIGHT"], "brightDense": c["BRIGHT_DENSE"],
            "nightPct": int(c["NIGHT_LIGHT"] * 100 + 0.5), "nightScale": c["NIGHT_SCALE"],
            "confirmUp": c["CONFIRM_UP"], "confirmDown": c["CONFIRM_DOWN"],
            "stepLight": c["STEP_MS_LIGHT"], "stepDense": c["STEP_MS_DENSE"],
        }

    def _load_eeprom(self):
        if not self.persist or not os.path.exists(EEPROM_FILE):
            return
        try:
            with open(EEPROM_FILE, encoding="utf-8") as fh:
                d = json.load(fh)
            b = d.get("baseline", 0)
            if self.cfgh["CAL_MIN_SIGNAL"] <= b < 1024:
                self.baseline, self.calibrated = float(b), True
            for k, v in d.get("cfg", {}).items():
                if k in CFG_LIMITS and CFG_LIMITS[k][0] <= v <= CFG_LIMITS[k][1]:
                    self.cfg[k] = v
        except (OSError, ValueError):
            pass

    def _save_eeprom(self):
        if not self.persist:
            return
        os.makedirs(os.path.dirname(EEPROM_FILE), exist_ok=True)
        with open(EEPROM_FILE, "w", encoding="utf-8") as fh:
            json.dump({"baseline": self.baseline if self.calibrated else 0, "cfg": self.cfg}, fh)

    def _p(self, text):
        self.out.append(text)

    def _boot(self):
        self._p(f"# Fog Illumination Uno v{self.fw}")
        self._print_help()
        self._print_cfg()
        self._print_cal()
        if not self.calibrated:
            self._p("# Not calibrated: in clear air hold the CAL button 2 s or send 'c'")
        else:
            self._p(f"# Calibration baseline = {fmt(self.baseline, 1)}")
        self._p(self.HEADER)

    def _print_help(self):
        self._p("# Commands: c=calibrate  d=demo on/off  p=next LCD page  i=info  h=help")
        self._p("# Settings: !get  !reset  !set key=value  (see README.id.md)")

    def _print_cfg(self):
        self._p("C," + ",".join(f"{k}={self.cfg[k]}" for k in CFG_LIMITS))

    def _print_cal(self):
        c = self.cfgh
        self._p(f"K,ldrLo={c['CAL_LDR_LO']},ldrHi={c['CAL_LDR_HI']},tGain={c['CAL_TEMP_GAIN']:.4f},"
                f"tOff={c['CAL_TEMP_OFFSET']:.2f},hGain={c['CAL_HUM_GAIN']:.4f},hOff={c['CAL_HUM_OFFSET']:.2f}")

    def _print_info(self):
        self._p(f"# Fog Illumination Uno v{self.fw}")
        self._p(f"# calibrated={'yes' if self.calibrated else 'no'} baseline={fmt(self.baseline, 1)}")
        feats = ["temp", "humidity", "dew_spread", "light", "attenuation"]
        for k in range(3):
            self._p(f"# W[{STATE_NAME[k]}] b={self.B[k]:.3f}" + "".join(
                f" {feats[i]}={self.W[k][i]:.3f}" for i in range(5)))

    def measure_laser(self):
        total, valid = 0, 0
        for _ in range(self.cfgh["LASER_CYCLES"]):
            off, on = self._adc_pair()
            if off >= self.cfgh["RX_SAT_ADC"] or on >= self.cfgh["RX_SAT_ADC"]:
                continue
            total += on - off
            valid += 1
        return total / valid if valid else float("nan")

    def calibrate(self):
        c = self.cfgh
        s, lo, hi = 0.0, 1e9, -1e9
        for _ in range(8):
            v = self.measure_laser()
            if v != v:
                self._p("# CAL FAILED: receiver saturated. Shade the receiver or lower its load resistor.")
                return
            s += v
            lo, hi = min(lo, v), max(hi, v)
        s /= 8
        if s < c["CAL_MIN_SIGNAL"]:
            self._p(f"# CAL FAILED: laser signal {fmt(s, 1)} too low. Align laser with the receiver.")
            return
        if self.hum_rh == self.hum_rh and self.hum_rh > c["CAL_MAX_HUM"]:
            self._p(f"# CAL FAILED: humidity {fmt(self.hum_rh, 1)} % - air is not clear. Calibrate in clear air.")
            return
        if hi - lo > s * c["CAL_MAX_SPREAD"]:
            self._p("# CAL FAILED: laser signal unstable (something in the beam?). Try again.")
            return
        if self.hum_rh != self.hum_rh:
            self._p("# CAL WARNING: no DHT11 reading, clear air not verified.")
        self.baseline, self.calibrated = s, True
        self._save_eeprom()
        self.laser_bad_count = 0
        self.faults &= ~FAULT_LASER
        self._p(f"# CAL OK: baseline = {fmt(self.baseline, 1)}")

    def compute_features(self):
        t = 25.0 if self.temp_c != self.temp_c else self.temp_c
        h = 50.0 if self.hum_rh != self.hum_rh else self.hum_rh
        self.dew_spread = t - dew_point(t, h)
        f = self.feat
        f[0] = clampf((t - 25.0) / 10.0, -2, 3)
        f[1] = clampf((h - 70.0) / 20.0, -3.5, 1.5)
        f[2] = clampf(self.dew_spread / 5.0, 0, 4)
        c = self.cfgh
        f[3] = clampf((self.light_raw - c["CAL_LDR_LO"]) / float(c["CAL_LDR_HI"] - c["CAL_LDR_LO"]), 0, 1)
        f[4] = clampf(1.0 - self.laser_sig / self.baseline, -0.2, 1.2) if self.calibrated else 0.0

    def run_model(self):
        z = [self.B[k] + sum(self.W[k][i] * self.feat[i] for i in range(5)) for k in range(3)]
        zmax = max(z)
        e = [math.exp(v - zmax) for v in z]
        s = sum(e)
        self.prob = [v / s for v in e]
        self.pred = 0
        for k in range(3):
            if self.prob[k] > self.prob[self.pred]:
                self.pred = k

    def check_faults(self):
        c = self.cfgh
        if self.dht_fails >= c["DHT_MAX_FAILS"]:
            self.faults |= FAULT_DHT
        else:
            self.faults &= ~FAULT_DHT
        bad = (self.calibrated and self.laser_sig < self.baseline * c["LASER_FAULT_FRAC"] and
               self.dht_fails == 0 and self.hum_rh == self.hum_rh and self.hum_rh < c["LASER_FAULT_MAX_H"])
        self.laser_bad_count = min(self.laser_bad_count + 1, 250) if bad else 0
        if self.laser_bad_count >= c["FAULT_CONFIRM"]:
            self.faults |= FAULT_LASER
        else:
            self.faults &= ~FAULT_LASER

    def update_state(self):
        if self.pred == self.state:
            self.pending_count = 0
            return
        if self.pred != self.pending:
            self.pending, self.pending_count = self.pred, 0
        self.pending_count += 1
        need = self.cfg["confirmUp"] if self.pred > self.state else self.cfg["confirmDown"]
        if self.pending_count >= need:
            self._p(f"# STATE {STATE_NAME[self.state]} -> {STATE_NAME[self.pred]}")
            self.state, self.pending_count = self.pred, 0

    def current_mode(self):
        if self.demo:
            return self.demo_mode
        if (self.faults & FAULT_LASER) or ((self.faults & FAULT_DHT) and not self.calibrated):
            return MODE_FAULT
        if not self.calibrated and self.state < ST_LIGHT:
            return ST_LIGHT
        return self.state

    def brightness_for(self, mode):
        if mode == ST_CLEAR:
            return 0
        if mode == MODE_FAULT:
            return 255
        b = self.cfg["brightDense"] if mode == ST_DENSE else self.cfg["brightLight"]
        if self.feat[3] < self.cfg["nightPct"] / 100.0:
            b = b * self.cfg["nightScale"] // 100
        return b

    def sample_and_decide(self):
        if not self.started or self.first_dht or self.now - self.last_dht >= self.cfgh["DHT_EVERY_MS"]:
            self.first_dht = False
            self.last_dht = self.now
            r = self._read_dht11()
            if r:
                c = self.cfgh
                self.temp_c = r[0] * c["CAL_TEMP_GAIN"] + c["CAL_TEMP_OFFSET"]
                self.hum_rh = clampf(r[1] * c["CAL_HUM_GAIN"] + c["CAL_HUM_OFFSET"], 1, 100)
                self.dht_fails = 0
            elif self.dht_fails < 250:
                self.dht_fails += 1
        self.light_raw = int(clampf(round(1023 * self._ambient() + self.rnd.gauss(0, 3)), 0, 1023))
        ls = self.measure_laser()
        self.rx_sat = ls != ls
        if not self.rx_sat:
            self.laser_sig = ls
        self.compute_features()
        self.run_model()
        self.check_faults()
        self.update_state()
        f, p = self.feat, self.prob
        self._p("D," + ",".join([
            str(self.now), fmt(self.temp_c, 1), fmt(self.hum_rh, 1), fmt(self.dew_spread, 2),
            str(self.light_raw), fmt(self.laser_sig, 1), fmt(self.baseline, 1),
            *[fmt(x, 4) for x in f], *[fmt(x, 3) for x in p],
            str(self.pred), str(self.state), str(self.faults), "1" if self.demo else "0"]))
        mode = self.current_mode()
        self._p(f"S,{mode},{self.brightness_for(mode)},{1 if self.calibrated else 0},{self.dht_fails},{1 if self.rx_sat else 0}")

    # ------------------------------------------------------------- serial input (same parser as handleSerial)
    def _handle_line(self, s):
        if s == "get":
            self._print_cfg()
            return self._print_cal()
        if s == "reset":
            self.cfg_defaults()
            self._save_eeprom()
            return self._print_cfg()
        if s.startswith("set ") and "=" in s[4:]:
            k, v = s[4:].split("=", 1)
            if k in CFG_LIMITS:
                try:
                    n = int(float(v)) if re.match(r"^-?\d+(\.\d*)?$", v.strip()) else 0  # atol()
                except ValueError:
                    n = 0
                lo, hi = CFG_LIMITS[k]
                self.cfg[k] = max(lo, min(hi, n))
                self._save_eeprom()
                return self._print_cfg()
        self._p("# ERR bad line command (!get, !reset, !set key=value)")

    def _handle_serial(self):
        rx, self.rx = self.rx, ""
        for c in rx:
            if self.in_line:
                if c in "\r\n":
                    self.in_line = False
                    if self.line:
                        self._handle_line(self.line)
                elif len(self.line) < 39:
                    self.line += c
                continue
            if c == "!":
                self.in_line, self.line = True, ""
            elif c in "cC":
                self.calibrate()
            elif c in "dD":
                self.demo = not self.demo
                self.demo_mode, self.demo_since = ST_CLEAR, self.now
                self._p("# DEMO ON" if self.demo else "# DEMO OFF")
            elif c in "iI":
                self._print_info()
            elif c in "hH?":
                self._print_help()
            # 'p' (next LCD page) has no effect: the simulator has no LCD

    def _loop(self):
        self._handle_serial()
        if not self.started or self.now - self.last_sample >= self.cfgh["SAMPLE_MS"]:
            self.started = True
            self.last_sample = self.now
            self.sample_and_decide()
        if self.demo and self.now - self.demo_since >= self.cfgh["DEMO_STEP_MS"]:
            self.demo_since = self.now
            self.demo_mode = (self.demo_mode + 1) % 4


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default="auto", choices=list(SCENARIOS))
    ap.add_argument("--speed", type=float, default=1.0, help="virtual time per real second")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this many real seconds (0 = forever)")
    args = ap.parse_args()
    sim = FogSim(args.scenario, args.speed, persist=False)
    t0 = time.monotonic()
    try:
        while not args.seconds or time.monotonic() - t0 < args.seconds:
            for ln in sim.lines():
                print(ln)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
