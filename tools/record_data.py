"""
Record labelled training data from the Arduino Uno over USB.

  python tools/record_data.py              (asks for the COM port if there are several)
  python tools/record_data.py --port COM7 --note humid_nofog

While it runs, press a key to set the label of the rows being recorded:
  0 = clear      1 = light fog      2 = dense fog      SPACE = pause (not recording)
  d = day        t = twilight       g = night (dark)   sets the light_cond column (kept until changed)
  n = change scenario note          c = calibrate the device (clear air!)
  q = quit

Every recording session is saved to data/train_YYYYmmdd_HHMMSS.csv.
Rows with a sensor fault or recorded in demo mode are skipped.

Scenario notes (free text, used later by train_model.py --compare):
  dry_clear, humid_nofog, dark_nofog, light_fog, dense_fog, laser_blocked ...
No extra libraries needed (pure Python, Windows).
"""
import argparse
import csv
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from serial_port import LineReader, choose_port  # noqa: E402

try:
    import msvcrt
except ImportError:
    sys.exit("This recorder uses the Windows keyboard (msvcrt). Run it on Windows.")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, "data")
LABELS = {"0": "clear", "1": "light_fog", "2": "dense_fog"}
LIGHT_LABELS = {"d": "day", "t": "twilight", "g": "night"}  # g = "gelap"; n is already the note key
STATE_NAMES = ["CLEAR", "LIGHT", "DENSE"]
DEFAULT_HEADER = ("ms,temp,hum,dew_spread,light_raw,laser_sig,baseline,f_temp,f_humidity,f_dew_spread,"
                  "f_light,f_attenuation,p_clear,p_light,p_dense,pred,state,fault,demo").split(",")


def status_line(label, note, light, row, counts):
    rec = f"REC {label:<9}" if label else "PAUSED       "
    try:
        att = float(row["f_attenuation"]) * 100
        light = float(row["f_light"]) * 100
        k = int(row["pred"])
        p = float(row[["p_clear", "p_light", "p_dense"][k]]) * 100
        s = (f"T={row['temp']:>5}C H={row['hum']:>5}% spread={float(row['dew_spread']):5.1f}C "
             f"light={light:3.0f}% att={att:4.0f}%  AI={STATE_NAMES[k]}({p:3.0f}%)")
    except (KeyError, ValueError, IndexError):
        s = "waiting for data..."
    flags = ""
    if row.get("fault", "0") not in ("0", ""):
        flags += " FAULT"
    if row.get("demo") == "1":
        flags += " DEMO"
    total = " ".join(f"{LABELS[k][0].upper()}{LABELS[k][1:3]}={counts[LABELS[k]]}" for k in LABELS)
    return f"[{rec}|{note or '-'}|{light or 'no light'}] {s}{flags}  [{total}]"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="e.g. COM7 (default: ask / auto)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--note", default="", help="scenario note stored with every row")
    args = ap.parse_args()

    port = choose_port(args.port)
    print(f"Opening {port} at {args.baud} baud (the Uno restarts, wait ~3 s)...")
    try:
        link = LineReader(port, args.baud)
    except OSError as e:
        sys.exit(str(e))

    print(__doc__.split("While it runs,")[1].split("Every recording")[0].rstrip())
    header = list(DEFAULT_HEADER)
    label, note, light = None, args.note, ""
    counts = {v: 0 for v in LABELS.values()}
    writer = fh = None
    path = None
    skipped = 0
    last_row = {}

    try:
        while True:
            while msvcrt.kbhit():
                key = msvcrt.getwch().lower()
                if key in LABELS:
                    label = LABELS[key]
                elif key in LIGHT_LABELS:
                    light = LIGHT_LABELS[key]
                elif key == " ":
                    label = None
                elif key == "c":
                    label = None
                    link.send("c")
                    print("\nCalibration requested (recording paused). Make sure the air is clear.")
                elif key == "n":
                    was, label = label, None
                    note = input("\nScenario note (e.g. humid_nofog): ").strip().replace(",", " ")
                    label = was
                elif key == "q":
                    raise KeyboardInterrupt
            for line in link.lines():
                if line.startswith("H,"):
                    header = line.split(",")[1:]
                elif line.startswith("#"):
                    print("\n" + line)
                elif line.startswith("D,"):
                    values = line.split(",")[1:]
                    if len(values) != len(header):
                        continue
                    row = dict(zip(header, values))
                    last_row = row
                    if label:
                        if row.get("fault", "0") != "0" or row.get("demo") == "1":
                            skipped += 1
                        else:
                            if writer is None:
                                os.makedirs(DATA_DIR, exist_ok=True)
                                path = os.path.join(DATA_DIR, datetime.now().strftime("train_%Y%m%d_%H%M%S.csv"))
                                fh = open(path, "w", newline="", encoding="utf-8")
                                writer = csv.writer(fh)
                                writer.writerow(["pc_time", "label", "note", "light_cond"] + header)
                            writer.writerow([datetime.now().isoformat(timespec="seconds"), label, note, light] + values)
                            fh.flush()
                            counts[label] += 1
                    sys.stdout.write("\r" + status_line(label, note, light, last_row, counts)[:150].ljust(150))
                    sys.stdout.flush()
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print(f"\n{e}")
    finally:
        link.close()
        if fh:
            fh.close()
    print()
    if path:
        print(f"Saved {sum(counts.values())} rows to {path}")
        print("  " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    else:
        print("Nothing recorded.")
    if skipped:
        print(f"Skipped {skipped} rows (sensor fault or demo mode).")
    print("Next step: python tools/train_model.py --compare")


if __name__ == "__main__":
    main()
