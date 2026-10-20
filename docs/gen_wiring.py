"""
Generate docs/wiring_diagram.svg — schematic-style wiring diagram.

  python docs/gen_wiring.py

Design rules (keep them if you edit this file):
  * Every component is drawn BETWEEN two exact points (res_between, led_between, ...),
    so a wire can never stop a few pixels short of a pin.
  * The Uno is drawn as a logical symbol: sensor pins on the left, output pins on the
    right, buttons on the bottom edge. Pin positions are chosen so that every net is a
    single straight line — no jogs and no crossings.
  * All coordinates sit on a 10 px grid.
"""
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wiring_diagram.svg")
W, H = 2040, 1580

# ---------------------------------------------------------------- net colours
C_5V, C_GND, C_BODY = "#dc2626", "#111827", "#111827"
C_DHT, C_LDR, C_RX, C_LASER, C_BTN, C_STAT = "#16a34a", "#0891b2", "#db2777", "#dc2626", "#7c3aed", "#ea580c"
C_LCD, C_595 = "#2563eb", "#0f766e"
GREY, SUB = "#6b7280", "#4b5563"

s = []


# ---------------------------------------------------------------- primitives
def t(x, y, text, size=11, anchor="start", bold=False, fill="#111827", mono=False):
    f = ' font-family="Consolas, monospace"' if mono else ""
    s.append(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" '
             f'font-weight="{"bold" if bold else "normal"}" fill="{fill}"{f}>{text}</text>')


def wire(pts, color=C_GND, width=2.4):
    d = " ".join(f"{x},{y}" for x, y in pts)
    s.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="{width}" '
             f'stroke-linecap="round" stroke-linejoin="round"/>')


def dot(p, color=C_GND, r=4.5):
    s.append(f'<circle cx="{p[0]}" cy="{p[1]}" r="{r}" fill="{color}"/>')


def box(x, y, w, h, fill="#ffffff", stroke=GREY, rx=8, sw=1.6):
    s.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
             f'stroke="{stroke}" stroke-width="{sw}"/>')


def _axis(p1, p2):
    return "h" if p1[1] == p2[1] else "v"


def _mid(p1, p2):
    return (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2


def _order(p1, p2):
    """Always draw a component left-to-right / top-to-bottom, whatever order it was given in."""
    if p1[0] > p2[0] or p1[1] > p2[1]:
        return p2, p1
    return p1, p2


def res_between(pa, pb, label="", side=None, color=C_BODY):
    """Resistor centred between two points; the leads are drawn for you."""
    p1, p2 = _order(pa, pb)
    cx, cy = _mid(p1, p2)
    if _axis(p1, p2) == "h":
        x0 = cx - 30
        wire([p1, (x0, p1[1])], color)
        s.append(f'<path d="M{x0},{p1[1]} l5,-7 l10,14 l10,-14 l10,14 l10,-14 l10,14 l5,-7" '
                 f'fill="none" stroke="{C_BODY}" stroke-width="2.2"/>')
        wire([(x0 + 60, p1[1]), p2], color)
        if label:
            t(cx, cy + 28 if side == "below" else cy - 18, label, 11, "middle", True)
    else:
        y0 = cy - 30
        wire([p1, (p1[0], y0)], color)
        s.append(f'<path d="M{p1[0]},{y0} l7,5 l-14,10 l14,10 l-14,10 l14,10 l-14,10 l7,5" '
                 f'fill="none" stroke="{C_BODY}" stroke-width="2.2"/>')
        wire([(p1[0], y0 + 60), p2], color)
        if label:
            off = -20 if side == "left" else 20
            t(cx + off, cy + 4, label, 11, "end" if side == "left" else "start", True)


def photoresistor_between(p1, p2, label="LDR"):
    res_between(p1, p2)
    cx, cy = _mid(p1, p2)
    for dy in (-12, 2):
        s.append(f'<line x1="{cx - 74}" y1="{cy + dy - 10}" x2="{cx - 34}" y2="{cy + dy + 2}" '
                 f'stroke="{C_LASER}" stroke-width="2" marker-end="url(#beam)"/>')
    t(cx - 80, cy - 6, label, 13, "end", True)


def led_between(p1, p2, color="#facc15", glow=True):
    """Horizontal LED, anode on the p1 side."""
    cx, cy = _mid(p1, p2)
    p1, p2 = _order(p1, p2)
    wire([p1, (cx - 10, cy)])
    s.append(f'<polygon points="{cx - 10},{cy - 11} {cx - 10},{cy + 11} {cx + 8},{cy}" '
             f'fill="{color}" stroke="{C_BODY}" stroke-width="1.6"/>')
    s.append(f'<line x1="{cx + 8}" y1="{cy - 11}" x2="{cx + 8}" y2="{cy + 11}" stroke="{C_BODY}" stroke-width="2.4"/>')
    wire([(cx + 8, cy), p2])
    if glow:
        for dx in (-8, 0):
            s.append(f'<line x1="{cx + dx}" y1="{cy - 14}" x2="{cx + dx + 10}" y2="{cy - 25}" '
                     f'stroke="{color}" stroke-width="1.8" marker-end="url(#glow)"/>')


def cap_between(pa, pb, label=""):
    p1, p2 = _order(pa, pb)
    cx, cy = _mid(p1, p2)
    wire([p1, (cx, cy - 5)])
    s.append(f'<line x1="{cx - 14}" y1="{cy - 5}" x2="{cx + 14}" y2="{cy - 5}" stroke="{C_BODY}" stroke-width="2.6"/>')
    s.append(f'<line x1="{cx - 14}" y1="{cy + 5}" x2="{cx + 14}" y2="{cy + 5}" stroke="{C_BODY}" stroke-width="2.6"/>')
    wire([(cx, cy + 5), p2])
    if label:
        t(cx - 20, cy + 4, label, 10, "end")


def switch_between(pa, pb):
    """Vertical push button; p1 is the top contact."""
    p1, p2 = _order(pa, pb)
    cx, cy = _mid(p1, p2)
    wire([p1, (cx, cy - 16)])
    s.append(f'<circle cx="{cx}" cy="{cy - 16}" r="4" fill="#ffffff" stroke="{C_BODY}" stroke-width="2"/>')
    s.append(f'<circle cx="{cx}" cy="{cy + 16}" r="4" fill="#ffffff" stroke="{C_BODY}" stroke-width="2"/>')
    s.append(f'<line x1="{cx + 14}" y1="{cy - 22}" x2="{cx + 14}" y2="{cy + 22}" stroke="{C_BODY}" stroke-width="2.6"/>')
    s.append(f'<line x1="{cx + 4}" y1="{cy - 16}" x2="{cx + 14}" y2="{cy - 16}" stroke="{C_BODY}" stroke-width="2"/>')
    s.append(f'<line x1="{cx + 4}" y1="{cy + 16}" x2="{cx + 14}" y2="{cy + 16}" stroke="{C_BODY}" stroke-width="2"/>')
    s.append(f'<line x1="{cx + 14}" y1="{cy}" x2="{cx + 34}" y2="{cy}" stroke="{C_BODY}" stroke-width="2"/>')
    s.append(f'<line x1="{cx + 34}" y1="{cy - 11}" x2="{cx + 34}" y2="{cy + 11}" stroke="{C_BODY}" stroke-width="3.2"/>')
    wire([(cx, cy + 16), p2])


def npn(cx, cy, mirror=False, base_lead=True):
    """
    NPN transistor drawn the conventional way: vertical base bar, collector and emitter
    leaving it at 45 degrees onto a common vertical rail, arrow on the emitter pointing
    away from the base. Returns (collector, base, emitter) — exact terminal points.

    mirror=False → rail on the right of the bar, base lead to the left
    mirror=True  → rail on the left of the bar, base lead to the right
    """
    sgn = -1 if mirror else 1
    bar = cx - 10 * sgn          # vertical base bar
    rail = cx + 14 * sgn         # collector/emitter rail
    s.append(f'<circle cx="{cx}" cy="{cy}" r="34" fill="#ffffff" stroke="{C_BODY}" stroke-width="1.6"/>')
    s.append(f'<line x1="{bar}" y1="{cy - 24}" x2="{bar}" y2="{cy + 24}" stroke="{C_BODY}" stroke-width="3.6"/>')
    # collector: diagonal off the bar, then straight to the terminal
    s.append(f'<line x1="{bar}" y1="{cy - 12}" x2="{rail}" y2="{cy - 26}" stroke="{C_BODY}" stroke-width="2.2"/>')
    s.append(f'<line x1="{rail}" y1="{cy - 26}" x2="{rail}" y2="{cy - 34}" stroke="{C_BODY}" stroke-width="2.2"/>')
    # emitter: diagonal with the arrow on it, then straight to the terminal
    s.append(f'<line x1="{bar}" y1="{cy + 12}" x2="{rail}" y2="{cy + 26}" stroke="{C_BODY}" stroke-width="2.2"/>')
    s.append(f'<line x1="{rail}" y1="{cy + 26}" x2="{rail}" y2="{cy + 34}" stroke="{C_BODY}" stroke-width="2.2"/>')
    dx, dy = rail - bar, 14.0
    ln = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / ln, dy / ln
    px, py = -uy, ux                                  # perpendicular
    tipx, tipy = bar + ux * ln * 0.92, cy + 12 + uy * ln * 0.92
    bx, by = bar + ux * ln * 0.42, cy + 12 + uy * ln * 0.42
    s.append(f'<polygon points="{tipx:.1f},{tipy:.1f} {bx + px * 5.4:.1f},{by + py * 5.4:.1f} '
             f'{bx - px * 5.4:.1f},{by - py * 5.4:.1f}" fill="{C_BODY}"/>')
    base = (bar - 26 * sgn, cy)
    if base_lead:
        s.append(f'<line x1="{bar}" y1="{cy}" x2="{base[0]}" y2="{cy}" stroke="{C_BODY}" stroke-width="2.2"/>')
    return (rail, cy - 34), base, (rail, cy + 34)


def phototransistor(cx, cy):
    """Phototransistor: an NPN with no base lead and two light arrows aimed at the base."""
    col, _, emi = npn(cx, cy, base_lead=False)
    for dy in (-12, 4):
        s.append(f'<line x1="{cx - 92}" y1="{cy + dy - 12}" x2="{cx - 46}" y2="{cy + dy + 2}" '
                 f'stroke="{C_LASER}" stroke-width="2.2" marker-end="url(#beam)"/>')
    return col, emi


def laser_diode_between(pa, pb):
    """Laser diode drawn vertically: anode on top, cathode bar at the bottom,
    with the two emission arrows of the laser-diode symbol."""
    p1, p2 = _order(pa, pb)
    cx = p1[0]
    cy = (p1[1] + p2[1]) / 2
    wire([p1, (cx, cy - 12)])
    s.append(f'<polygon points="{cx - 15},{cy - 12} {cx + 15},{cy - 12} {cx},{cy + 10}" '
             f'fill="#fecaca" stroke="{C_BODY}" stroke-width="2"/>')
    s.append(f'<line x1="{cx - 17}" y1="{cy + 10}" x2="{cx + 17}" y2="{cy + 10}" stroke="{C_BODY}" stroke-width="3"/>')
    wire([(cx, cy + 10), p2])
    for k in (0, 1):
        x0, y0 = cx + 22, cy - 6 + k * 13
        s.append(f'<line x1="{x0}" y1="{y0}" x2="{x0 + 30}" y2="{y0 - 22}" stroke="{C_LASER}" '
                 f'stroke-width="2.2" marker-end="url(#beam)"/>')


def gnd_at(p, stem=20):
    """Ground symbol hanging below p; the stem starts exactly at p."""
    x, y = p[0], p[1] + stem
    wire([p, (x, y)])
    s.append(f'<g stroke="{C_GND}" stroke-width="2.4"><line x1="{x - 17}" y1="{y}" x2="{x + 17}" y2="{y}"/>'
             f'<line x1="{x - 10}" y1="{y + 7}" x2="{x + 10}" y2="{y + 7}"/>'
             f'<line x1="{x - 4}" y1="{y + 14}" x2="{x + 4}" y2="{y + 14}"/></g>')


def v5_at(p, stem=20, up=True):
    """+5 V tag above (or below) p, joined by a stem."""
    x = p[0]
    y = p[1] - stem if up else p[1] + stem
    wire([p, (x, y)], C_5V)
    ty = y - 17 if up else y
    s.append(f'<rect x="{x - 14}" y="{ty}" width="28" height="17" rx="3" fill="{C_5V}"/>')
    t(x, ty + 13, "5V", 10, "middle", True, "#ffffff")


def sym_pin(p, label, side, num="", dark=True):
    """Pin dot on a symbol edge, with the pin name written inside the body."""
    s.append(f'<circle cx="{p[0]}" cy="{p[1]}" r="5" fill="#e5e7eb" stroke="#9ca3af" stroke-width="1.4"/>')
    col = "#ffffff" if dark else "#111827"
    grey = "#9ca3af" if dark else GREY
    num_r = f' <tspan fill="{grey}" font-weight="normal">{num}</tspan>' if num else ""
    num_l = f'<tspan fill="{grey}" font-weight="normal">{num}</tspan> ' if num else ""
    if side == "left":
        s.append(f'<text x="{p[0] + 15}" y="{p[1] + 5}" font-size="12.5" font-weight="bold" fill="{col}" '
                 f'font-family="Consolas, monospace">{label}{num_r}</text>')
    elif side == "right":
        s.append(f'<text x="{p[0] - 15}" y="{p[1] + 5}" font-size="12.5" text-anchor="end" font-weight="bold" '
                 f'fill="{col}" font-family="Consolas, monospace">{num_l}{label}</text>')
    elif side == "top":
        s.append(f'<text x="{p[0]}" y="{p[1] + 24}" font-size="12.5" text-anchor="middle" font-weight="bold" '
                 f'fill="{col}" font-family="Consolas, monospace">{label}</text>')
    else:
        s.append(f'<text x="{p[0]}" y="{p[1] - 16}" font-size="12.5" text-anchor="middle" font-weight="bold" '
                 f'fill="{col}" font-family="Consolas, monospace">{label}</text>')


# ================================================================ canvas
s.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
         f'font-family="Segoe UI, system-ui, Arial, sans-serif">')
s.append('<defs>'
         '<marker id="beam" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">'
         f'<path d="M0,0 L8,4 L0,8 z" fill="{C_LASER}"/></marker>'
         '<marker id="glow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">'
         '<path d="M0,0 L7,3.5 L0,7 z" fill="#d97706"/></marker>'
         '<marker id="flow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto">'
         '<path d="M0,0 L10,5 L0,10 z" fill="#f59e0b"/></marker>'
         '</defs>')
s.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
t(60, 62, "Fog-Resilient Smart Road Lane Illumination — Wiring Diagram", 26, "start", True)
t(60, 90, "Arduino UNO · DHT11 · LDR · laser + phototransistor · 74HC595 with 8 guiding LEDs · LCD 16×2 (I2C) · 2 buttons · status LED",
  13, "start", False, SUB)

# ================================================================ Uno symbol
BX0, BX1, BY0, BY1 = 700, 1000, 180, 1100
box(BX0, BY0, BX1 - BX0, BY1 - BY0, "#1f2937", "#0f172a", 12, 2)
t(BX0, BY0 - 18, "Arduino UNO R3", 16, "start", True)
box(BX0 + 60, 560, 180, 116, "#374151", "#4b5563", 6, 1.4)
t((BX0 + BX1) / 2, 606, "ATmega328P", 13, "middle", True, "#e5e7eb")
t((BX0 + BX1) / 2, 628, "16 MHz · 5 V", 11, "middle", False, "#9ca3af")
t((BX0 + BX1) / 2, 656, "D0/D1 = USB serial (free)", 10, "middle", False, "#9ca3af")
t((BX0 + BX1) / 2, 700, "D6–D10, A3 = free", 10, "middle", False, "#9ca3af")

P_D2, P_A0, P_A1, P_A2 = (BX0, 240), (BX0, 480), (BX0, 720), (BX0, 1010)
for p, name in ((P_D2, "D2"), (P_A0, "A0"), (P_A1, "A1"), (P_A2, "A2")):
    sym_pin(p, name, "left")
P_D13, P_D12, P_D11 = [(BX1, y) for y in (220, 304, 388)]
P_A4, P_A5 = (BX1, 556), (BX1, 640)   # I2C: SDA = A4, SCL = A5
P_D5 = (BX1, 1060)                     # status LED = D5
for p, name in ((P_D13, "D13"), (P_D12, "D12"), (P_D11, "D11"), (P_A4, "A4 SDA"), (P_A5, "A5 SCL"),
                (P_D5, "D5")):
    sym_pin(p, name, "right")
P_5V = (900, BY0)
sym_pin(P_5V, "5V", "top")
v5_at(P_5V, 40)
P_CAL, P_PAGE, P_GNDB = (760, BY1), (880, BY1), (960, BY1)
for p, name in ((P_CAL, "D3"), (P_PAGE, "D4"), (P_GNDB, "GND")):
    sym_pin(p, name, "bottom")

# ================================================================ DHT11 → D2
box(150, 186, 280, 108)
t(172, 222, "DHT11", 16, "start", True)
t(172, 246, "temperature + humidity", 11, "start", False, SUB)
t(172, 276, "single-wire digital sensor", 10, "start", False, GREY)
sym_pin((430, 212), "VCC", "right", dark=False)
sym_pin((430, 240), "DATA", "right", dark=False)
sym_pin((430, 272), "GND", "right", dark=False)
wire([(430, 212), (480, 212)], C_5V)
v5_at((480, 212), 40)
wire([(430, 272), (520, 272)])
gnd_at((520, 272))
wire([(430, 240), P_D2], C_DHT)
dot((610, 240), C_DHT)
res_between((610, 240), (610, 150), "10 kΩ", "left")
v5_at((610, 150), 24)

# ================================================================ LDR → A0
v5_at((300, 390), 26)
photoresistor_between((300, 390), (300, 480))
dot((300, 480), C_LDR)
wire([(300, 480), P_A0], C_LDR)
res_between((300, 480), (300, 560), "10 kΩ")
gnd_at((300, 560))
t(214, 462, "ambient light", 11, "end", False, SUB)

# ================================================================ Phototransistor → A1
col, emi = phototransistor(286, 686)
v5_at(col, 20)
dot(emi, C_RX)
wire([emi, P_A1], C_RX)
res_between(emi, (300, 800), "10 kΩ")
gnd_at((300, 800))
t(186, 650, "Phototransistor", 13, "end", True)
t(186, 668, "laser receiver", 10, "end", False, SUB)
t(186, 684, "inside a black tube", 10, "end", False, SUB)

# ================================================================ Laser + Q1 → A2
v5_at((280, 880), 26)
laser_diode_between((280, 880), (280, 946))
t(246, 906, "LD1", 12, "end", True)
t(246, 922, "laser diode", 10, "end", False, SUB)
t(246, 938, "≤ 5 mW · 650 nm", 10, "end", False, SUB)
t(300, 872, "+", 11, "start", True, "#7f1d1d")
t(300, 958, "−", 11, "start", True, "#7f1d1d")
lcol, lbase, lemi = npn(294, 1010, mirror=True)
wire([(280, 946), lcol])
gnd_at(lemi, 24)
res_between(lbase, P_A2, "1 kΩ")
t(300, 1094, "Q1 · 2N2222 / BC547", 11, "middle", True)
t(300, 1110, "switches the laser on and off for every reading", 10, "middle", False, SUB)
s.append(f'<line x1="348" y1="872" x2="456" y2="826" stroke="{C_LASER}" stroke-width="2.2" '
         f'stroke-dasharray="8 6" marker-end="url(#beam)"/>')
t(468, 836, "laser beam → fog chamber (30–50 cm) → phototransistor", 10, "start", False, "#b91c1c")
t(468, 854, "ready-made module: series resistor is built in", 10, "start", False, GREY)
t(468, 870, "bare diode: add ≈ 100 Ω in series", 10, "start", False, GREY)

# ================================================================ Buttons + board ground
for p in (P_CAL, P_PAGE):
    wire([p, (p[0], 1150)], C_BTN)
    switch_between((p[0], 1150), (p[0], 1230))
    gnd_at((p[0], 1230))
t(700, 1300, "CAL button", 12, "middle", True)
t(700, 1318, "hold 2 s = calibrate", 10, "middle", False, SUB)
t(900, 1300, "PAGE button", 12, "middle", True)
t(900, 1318, "short = page · 2 s = demo", 10, "middle", False, SUB)
t(800, 1342, "both wired to ground with INPUT_PULLUP — no external resistor", 10, "middle", False, GREY)
wire([P_GNDB, (960, 1170)])
gnd_at((960, 1170))

# ================================================================ Status LED → D5
wire([P_D5, (1050, 1060), (1050, 1330)], C_STAT)
res_between((1050, 1330), (1170, 1330), "330 Ω")
led_between((1170, 1330), (1250, 1330), "#f59e0b")
wire([(1250, 1330), (1290, 1330)])
gnd_at((1290, 1330))
t(1180, 1382, "Status LED — fog / fault / not calibrated", 11, "middle", True)

# ================================================================ 74HC595 + guiding LEDs
CX0, CX1, CY0, CY1 = 1150, 1330, 170, 530
t(1390, 130, "GUIDING LIGHT — eight LEDs on a shift register", 15, "start", True)
box(CX0, CY0, CX1 - CX0, CY1 - CY0, "#1f2937", "#0f172a", 8, 2)
s.append(f'<path d="M{(CX0 + CX1) / 2 - 12},{CY0} a12,12 0 0,0 24,0" fill="#ffffff"/>')
t((CX0 + CX1) / 2, CY1 + 28, "U1 · 74HC595", 13, "middle", True)
t((CX0 + CX1) / 2, CY1 + 46, "grey numbers = chip pins", 10, "middle", False, GREY)

for p_mcu, cy, name, num, netlabel in ((P_D13, 220, "SH_CP", "11", "CLOCK"), (P_D12, 304, "ST_CP", "12", "LATCH"),
                                       (P_D11, 388, "DS", "14", "DATA")):
    sym_pin((CX0, cy), name, "left", num=num)
    wire([p_mcu, (CX0, cy)], C_595)
    t((BX1 + CX0) / 2, cy - 12, netlabel, 10, "middle", True, C_595)

sym_pin((CX0, 472), "OE", "left", num="13")
wire([(CX0, 472), (1100, 472)])
gnd_at((1100, 472))
t((BX1 + CX0) / 2, 460, "OE to ground", 10, "middle", True, C_595)
sym_pin((1200, CY0), "VCC", "top")
sym_pin((1270, CY0), "MR", "top")
wire([(1200, CY0), (1200, 110)], C_5V)
wire([(1270, CY0), (1270, 110)], C_5V)
wire([(1200, 110), (1270, 110)], C_5V)
dot((1200, 110), C_5V)
dot((1270, 110), C_5V)
v5_at((1235, 110), 22)
dot((1235, 110), C_5V)
wire([(1040, 110), (1200, 110)], C_5V)
cap_between((1040, 110), (1040, 152), "100 nF")
gnd_at((1040, 152))
sym_pin((CX0, 500), "GND", "left", num="8")
wire([(CX0, 500), (1070, 500)])
gnd_at((1070, 500))

LED_Y = [200 + i * 40 for i in range(8)]
BUS_X = 1660
for i, y in enumerate(LED_Y):
    sym_pin((CX1, y), f"Q{i}", "right", num=("15" if i == 0 else str(i)))
    wire([(CX1, y), (1390, y)], C_595)
    res_between((1390, y), (1500, y))
    led_between((1500, y), (1580, y))
    wire([(1580, y), (BUS_X, y)])
    if i:
        dot((BUS_X, y))
    t(1700, y + 5, f"LED{i + 1}", 12, "start", True, "#a16207")
wire([(BUS_X, LED_Y[0]), (BUS_X, LED_Y[-1])])
gnd_at((BUS_X, LED_Y[-1]))
t(1445, 172, "470 Ω on every output", 11, "middle", True)
t(1790, 206, "LED1 = start of the lane", 11, "start", False, SUB)
t(1790, 246, "anode (+) → 470 Ω → Qn", 10, "start", False, SUB)
t(1790, 264, "cathode (−) → common ground", 10, "start", False, SUB)
t(1790, 300, "≈ 6 mA per LED · chip max 70 mA", 10, "start", False, SUB)
t(1790, 340, "OE (pin 13) tied to ground —", 10, "start", True, "#b45309")
t(1790, 358, "LEDs on/off only, no PWM dimming", 10, "start", False, SUB)
t(1790, 394, "Q7′ (pin 9) → DS of a second 595", 10, "start", False, GREY)
t(1790, 412, "if you ever want 16 LEDs", 10, "start", False, GREY)
t(1790, 486, "LED8 = end of the lane", 11, "start", False, SUB)

# ================================================================ LCD 16×2 (I2C backpack)
LX0, LX1, LY0, LY1 = 1150, 1900, 540, 1090
box(LX0, LY0, LX1 - LX0, LY1 - LY0, "#166534", "#14532d", 10, 2)
t(LX0 + 26, LY0 + 36, "LCD 16×2 + I2C backpack (PCF8574)", 15, "start", True, "#ffffff")
box(1440, 600, 420, 250, "#1d4ed8", "#1e3a8a", 6, 2)
s.append('<text x="1470" y="700" font-size="27" fill="#e0f2fe" font-family="Consolas, monospace">DENSE FOG  94%</text>')
s.append('<text x="1470" y="790" font-size="27" fill="#e0f2fe" font-family="Consolas, monospace">Guide: SLOW DOWN</text>')
for p_mcu, name in ((P_A4, "SDA"), (P_A5, "SCL")):
    sym_pin((LX0, p_mcu[1]), name, "left")
    wire([p_mcu, (LX0, p_mcu[1])], C_LCD)
t((BX1 + LX0) / 2, 544, "I2C: SDA + SCL", 10, "middle", True, C_LCD)
t(LX0 + 26, LY0 + 60, "I2C address 0x27 (0x3F on some boards)", 11, "start", False, "#bbf7d0")

# power pins of the backpack
sym_pin((LX1, 900), "VCC", "right")
wire([(LX1, 900), (1950, 900)], C_5V)
v5_at((1950, 900), 24)
s.append(f'<circle cx="1750" cy="{LY1}" r="5" fill="#e5e7eb" stroke="#9ca3af" stroke-width="1.4"/>')
s.append(f'<text x="1750" y="{LY1 - 16}" font-size="12.5" text-anchor="middle" font-weight="bold" fill="#ffffff" '
         f'font-family="Consolas, monospace">GND</text>')
wire([(1750, LY1), (1750, 1180)])
gnd_at((1750, 1180))
t(1750, 1230, "contrast: small trimmer on the backpack", 10, "middle", False, SUB)

# ================================================================ legend
box(60, 1130, 600, 180, "#f9fafb", "#e5e7eb", 10, 1.4)
t(86, 1162, "LEGEND", 12, "start", True)
s.append('<rect x="86" y="1176" width="28" height="17" rx="3" fill="#dc2626"/>')
t(100, 1189, "5V", 10, "middle", True, "#ffffff")
t(126, 1190, "+5 V net (from USB)", 11, "start")
s.append(f'<g stroke="{C_GND}" stroke-width="2.4"><line x1="364" y1="1186" x2="398" y2="1186"/>'
         f'<line x1="371" y1="1193" x2="391" y2="1193"/><line x1="377" y1="1200" x2="385" y2="1200"/></g>')
s.append(f'<line x1="381" y1="1176" x2="381" y2="1186" stroke="{C_GND}" stroke-width="2.4"/>')
t(412, 1190, "ground — one common net", 11, "start")
dot((100, 1222))
t(126, 1226, "junction = wires joined", 11, "start")
t(364, 1226, "grey numbers = chip pin numbers", 11, "start")
nets = [("DHT11 DATA (D2)", C_DHT), ("LDR (A0)", C_LDR), ("Phototransistor (A1)", C_RX), ("Laser drive (A2)", C_LASER),
        ("Buttons (D3 / D4)", C_BTN), ("Status LED (D5)", C_STAT), ("LCD I2C (A4 / A5)", C_LCD), ("74HC595 (D11–D13)", C_595)]
for i, (name, col) in enumerate(nets):
    x = 86 + (i % 2) * 278
    y = 1252 + (i // 2) * 18
    wire([(x, y), (x + 26, y)], col, 3)
    t(x + 34, y + 4, name, 11, "start")

# ================================================================ physical placement
t(60, 1382, "PHYSICAL PLACEMENT — three LEDs travel along the lane, slower in dense fog", 13, "start", True)
box(60, 1398, 560, 142, "#374151", "#1f2937", 10, 2)
s.append('<line x1="80" y1="1428" x2="600" y2="1428" stroke="#e5e7eb" stroke-width="3" stroke-dasharray="22 14"/>')
s.append('<line x1="86" y1="1458" x2="594" y2="1458" stroke="#f59e0b" stroke-width="2" marker-end="url(#flow)"/>')
t(340, 1452, "driving direction", 10, "middle", True, "#fbbf24")
for i in range(8):
    cx = 100 + i * 63
    vals = ";".join("1" if (k - i) % 8 in (0, 1, 2) else "0.15" for k in range(8))
    s.append(f'<circle cx="{cx}" cy="1494" r="12" fill="#facc15" stroke="#a16207" stroke-width="1.6">'
             f'<animate attributeName="fill-opacity" values="{vals}" dur="1.6s" calcMode="discrete" repeatCount="indefinite"/></circle>')
    t(cx, 1524, f"{i + 1}", 10, "middle", True, "#e5e7eb")
t(80, 1420, "lane model inside the fog chamber", 10, "start", False, "#9ca3af")

# ================================================================ notes
box(660, 1398, 1340, 142, "#fff7ed", "#fdba74", 10, 1.4)
t(686, 1426, "POWER &amp; NOTES", 12, "start", True)
left_notes = [
    "• Everything runs from the USB 5 V rail — about 165 mA in total.",
    "• 74HC595: VCC and MR to 5 V, GND to ground, 100 nF across the supply pins,",
    "   and OE (pin 13) straight to ground. Data, latch and clock are plain D11–D13.",
    "• Each LED draws ≈ 6 mA (470 Ω); one 595 carries 70 mA in total. For brighter",
    "   road LEDs use a TPIC6B595 or add ULN2803 drivers.",
]
right_notes = [
    "• LCD: I2C backpack — SDA to A4, SCL to A5, VCC 5 V, GND. Blank screen: turn the trimmer.",
    "• The laser is switched by Q1, never straight from a pin. Keep the phototransistor",
    "   in a black tube, aligned with the beam, 30–50 cm away.",
    "• D6–D10, A3 and D0/D1 (USB serial, AI data recording) stay free.",
    "• Every ground symbol is the same net — join all grounds on the breadboard.",
]
for i, line in enumerate(left_notes):
    t(686, 1450 + i * 19, line, 11, "start", False, "#374151")
for i, line in enumerate(right_notes):
    t(1330, 1450 + i * 19, line, 11, "start", False, "#374151")

s.append("</svg>")
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(s))
print("wrote", OUT)
