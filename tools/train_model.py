"""
Train the edge-AI model (softmax logistic regression, 3 classes) from data
recorded with tools/record_data.py, and compare the sensors.

  python tools/train_model.py              train on all 5 features, show the test result
  python tools/train_model.py --compare    also compare single sensors / combinations
  python tools/train_model.py --install    write the weights into the firmware folder

Then re-upload firmware/fog_illumination_uno to the Uno.
No extra libraries needed (pure Python).
"""
import argparse
import csv
import glob
import math
import os
import random
from collections import Counter, defaultdict

FEATURES = ["temp", "humidity", "dew_spread", "light", "attenuation"]
CLASSES = ["clear", "light_fog", "dense_fog"]
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, "data")
FIRMWARE_HEADER = os.path.join(BASE, "firmware", "fog_illumination_uno", "model_weights.h")

# Feature sets compared with --compare (the "which sensor matters" table for the report)
COMPARE_SETS = [
    ("Temperature only (DHT11)", ["temp"]),
    ("Humidity only (DHT11)", ["humidity"]),
    ("Dew-point spread only (DHT11)", ["dew_spread"]),
    ("Ambient light only (LDR)", ["light"]),
    ("Laser attenuation only", ["attenuation"]),
    ("DHT11 all (T, RH, spread)", ["temp", "humidity", "dew_spread"]),
    ("Laser + humidity", ["attenuation", "humidity"]),
    ("All 5 features (fusion)", FEATURES),
]


def load(pattern):
    X, y, notes = [], [], []
    files = sorted(glob.glob(pattern))
    for path in files:
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("label") not in CLASSES:
                    continue
                try:
                    X.append([float(row["f_" + f]) for f in FEATURES])
                except (KeyError, ValueError):
                    continue
                y.append(CLASSES.index(row["label"]))
                notes.append(row.get("note") or "-")
        print(f"  {os.path.relpath(path, BASE)}")
    return X, y, notes


def softmax(z):
    m = max(z)
    e = [math.exp(v - m) for v in z]
    s = sum(e)
    return [v / s for v in e]


def predict_proba(W, B, x):
    return softmax([b + sum(w * v for w, v in zip(Wk, x)) for Wk, b in zip(W, B)])


def train(X, y, epochs=800, lr=0.1, l2=0.0005):
    """Full-batch gradient descent with the Adam optimiser."""
    n, d, K = len(X), len(X[0]), len(CLASSES)
    cnt = Counter(y)
    cw = {k: n / (K * cnt[k]) for k in cnt}  # balance the classes
    W = [[0.0] * d for _ in range(K)]
    B = [0.0] * K
    # Adam moments for every parameter; parameter layout = W row by row, then B
    npar = K * (d + 1)
    m1, m2 = [0.0] * npar, [0.0] * npar
    b1, b2, eps = 0.9, 0.999, 1e-8
    for t in range(1, epochs + 1):
        gW = [[0.0] * d for _ in range(K)]
        gB = [0.0] * K
        for xi, yi in zip(X, y):
            p = predict_proba(W, B, xi)
            for k in range(K):
                err = (p[k] - (1.0 if k == yi else 0.0)) * cw[yi]
                gB[k] += err
                row = gW[k]
                for j in range(d):
                    row[j] += err * xi[j]
        grads = [gW[k][j] / n + l2 * W[k][j] for k in range(K) for j in range(d)] + [g / n for g in gB]
        params = [W[k][j] for k in range(K) for j in range(d)] + B
        c1, c2 = 1 - b1 ** t, 1 - b2 ** t
        for i, g in enumerate(grads):
            m1[i] = b1 * m1[i] + (1 - b1) * g
            m2[i] = b2 * m2[i] + (1 - b2) * g * g
            params[i] -= lr * (m1[i] / c1) / (math.sqrt(m2[i] / c2) + eps)
        W = [params[k * d:(k + 1) * d] for k in range(K)]
        B = params[K * d:]
    return W, B


def confusion(W, B, X, y):
    K = len(CLASSES)
    m = [[0] * K for _ in range(K)]
    for xi, yi in zip(X, y):
        p = predict_proba(W, B, xi)
        m[yi][p.index(max(p))] += 1
    return m


def scores(m):
    K = len(m)
    total = sum(map(sum, m)) or 1
    acc = sum(m[k][k] for k in range(K)) / total
    recall = [m[k][k] / (sum(m[k]) or 1) for k in range(K)]
    precision = [m[k][k] / (sum(m[r][k] for r in range(K)) or 1) for k in range(K)]
    return acc, recall, precision


def split(y, test_frac=0.2, seed=42, block=30):
    """Stratified split in contiguous blocks of recorded rows.

    Rows are logged once per second, so neighbouring rows are near-duplicates.
    Splitting row by row would put them in both sets and inflate the test score;
    whole blocks go to either train or test instead.
    """
    rnd = random.Random(seed)
    by = defaultdict(list)
    for i, c in enumerate(y):
        by[c].append(i)
    tr, te = [], []
    for idx in by.values():
        size = max(1, min(block, len(idx) // 5))  # keep >= 5 blocks per class
        blocks = [idx[s:s + size] for s in range(0, len(idx), size)]
        rnd.shuffle(blocks)
        want = max(1, int(len(idx) * test_frac))
        got = 0
        for b in blocks:
            if got < want and len(b) < len(idx):
                te += b
                got += len(b)
            else:
                tr += b
    return tr, te


def subset(X, cols):
    ids = [FEATURES.index(c) for c in cols]
    return [[x[i] for i in ids] for x in X]


def print_report(m, title):
    acc, rec, prec = scores(m)
    print(f"\n{title}")
    print(f"  Accuracy: {acc * 100:.1f}%")
    print("  Confusion matrix (rows = true, columns = predicted):")
    print("               " + "".join(f"{c:>11}" for c in CLASSES))
    for k, c in enumerate(CLASSES):
        print(f"  {c:>11}  " + "".join(f"{v:>11}" for v in m[k]))
    for k, c in enumerate(CLASSES):
        print(f"  {c:>11}: recall {rec[k] * 100:5.1f}%   precision {prec[k] * 100:5.1f}%")
    if rec[2] < 0.9:
        print("  WARNING: dense-fog recall < 90 % - record more dense-fog data or check the laser alignment.")


def compare(X, y, notes, tr, te):
    rows = []
    print("\nSensor comparison (same train/test split, test data):")
    print(f"  {'Feature set':<32}{'Accuracy':>9}{'Clear':>8}{'Light':>8}{'Dense':>8}")
    for name, cols in COMPARE_SETS:
        Xs = subset(X, cols)
        W, B = train([Xs[i] for i in tr], [y[i] for i in tr])
        m = confusion(W, B, [Xs[i] for i in te], [y[i] for i in te])
        acc, rec, _ = scores(m)
        rows.append([name, "+".join(cols), acc] + rec)
        print(f"  {name:<32}{acc * 100:8.1f}%{rec[0] * 100:7.1f}%{rec[1] * 100:7.1f}%{rec[2] * 100:7.1f}%")

    # Leave-one-out: how much does the fusion model lose without each feature?
    full_acc = rows[-1][2]
    print("\nDrop-one test (accuracy lost when a feature is removed):")
    drop = []
    for f in FEATURES:
        cols = [c for c in FEATURES if c != f]
        Xs = subset(X, cols)
        W, B = train([Xs[i] for i in tr], [y[i] for i in tr])
        acc, _, _ = scores(confusion(W, B, [Xs[i] for i in te], [y[i] for i in te]))
        drop.append((f, full_acc - acc))
        print(f"  without {f:<12} {acc * 100:6.1f}%   (loss {(full_acc - acc) * 100:+.1f} points)")

    # Accuracy per scenario note for the fusion model (shows the tricky cases)
    W, B = train([X[i] for i in tr], [y[i] for i in tr])
    per = defaultdict(lambda: [0, 0])
    for i in te:
        p = predict_proba(W, B, X[i])
        per[notes[i]][0] += p.index(max(p)) == y[i]
        per[notes[i]][1] += 1
    if len(per) > 1 or "-" not in per:
        print("\nFusion model accuracy per scenario note (test data):")
        for note, (ok, n) in sorted(per.items()):
            print(f"  {note:<20} {ok}/{n}  ({ok / n * 100:.0f}%)")

    os.makedirs(DATA_DIR, exist_ok=True)
    out_csv = os.path.join(DATA_DIR, "sensor_comparison.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["feature_set", "features", "accuracy", "recall_clear", "recall_light", "recall_dense"])
        for r in rows:
            w.writerow(r[:2] + [f"{v:.4f}" for v in r[2:]])
        w.writerow([])
        w.writerow(["dropped_feature", "accuracy_loss"])
        for f, loss in drop:
            w.writerow([f, f"{loss:.4f}"])
    print(f"\nSaved table to {os.path.relpath(out_csv, BASE)} (open in Excel for the report).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(DATA_DIR, "train_*.csv"))
    ap.add_argument("--compare", action="store_true", help="compare sensors / feature sets")
    ap.add_argument("--install", action="store_true", help="write model_weights.h into the firmware folder")
    args = ap.parse_args()

    print("Loading data:")
    X, y, notes = load(args.data)
    cnt = Counter(y)
    missing = [CLASSES[k] for k in range(len(CLASSES)) if cnt[k] < 5]
    if missing:
        print(f"Need at least 5 rows of every class; missing/too few: {', '.join(missing)}.")
        print("Record them with: python tools/record_data.py")
        return
    print(f"Total {len(X)} rows: " + ", ".join(f"{c}={cnt[k]}" for k, c in enumerate(CLASSES)))

    tr, te = split(y)
    W, B = train([X[i] for i in tr], [y[i] for i in tr])
    print_report(confusion(W, B, [X[i] for i in te], [y[i] for i in te]),
                 f"Test result on {len(te)} unseen rows (20 %):")

    # Feature importance: |weight| x spread of the feature, relative to the clear class
    print("\nFeature influence (|w_dense - w_clear| x std of feature):")
    for j, f in enumerate(FEATURES):
        col = [x[j] for x in X]
        mu = sum(col) / len(col)
        sd = math.sqrt(sum((v - mu) ** 2 for v in col) / len(col))
        print(f"  {f:<12} {abs(W[2][j] - W[0][j]) * sd:7.3f}")

    if args.compare:
        compare(X, y, notes, tr, te)

    # Final model on ALL data for the firmware
    W, B = train(X, y)
    rows = ",\n".join("  {" + ", ".join(f"{v:.4f}f" for v in Wk) + "}" for Wk in W)
    header = (
        "#pragma once\n"
        "// Generated by tools/train_model.py\n"
        f"// Trained on {len(X)} rows: " + ", ".join(f"{c}={cnt[k]}" for k, c in enumerate(CLASSES)) + "\n"
        "// Classes: 0 = CLEAR, 1 = LIGHT FOG, 2 = DENSE FOG\n"
        f"// Features: {', '.join(FEATURES)}\n"
        f"#define MODEL_N_FEATURES {len(FEATURES)}\n"
        f"#define MODEL_N_CLASSES {len(CLASSES)}\n"
        "static const float MODEL_W[MODEL_N_CLASSES][MODEL_N_FEATURES] = {\n" + rows + ",\n};\n"
        "static const float MODEL_B[MODEL_N_CLASSES] = {" + ", ".join(f"{b:.4f}f" for b in B) + "};\n"
    )
    out = FIRMWARE_HEADER if args.install else os.path.join(DATA_DIR, "model_weights.h")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(header)
    print("\n" + header)
    print(f"Saved to {os.path.relpath(out, BASE)}")
    if not args.install:
        print("Run again with --install to put it in the firmware, then re-upload to the Uno.")
    else:
        print("Now open firmware/fog_illumination_uno in Arduino IDE and upload it again.")


if __name__ == "__main__":
    main()
