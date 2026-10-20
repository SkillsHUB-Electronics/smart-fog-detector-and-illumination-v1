// ============================================================================
//  Fog-Resilient Smart Road Lane Illumination System — Arduino Uno firmware
//
//  Sensors : DHT11 (temperature, humidity -> dew-point spread), LDR (ambient
//            light), laser + phototransistor (fog attenuation, laser ON/OFF
//            differential measurement so sunlight does not matter)
//  Brain   : edge AI (softmax logistic regression, 3 classes) + fail-safe rules
//  Output  : chasing guiding lights through a 74HC595 (LEDs on/off, no PWM),
//            LCD 16x2 (I2C), status LED, CSV telemetry over USB
//
//  Libraries: EEPROM (built in) and LiquidCrystal_I2C (Library Manager).
//  The DHT11 is read by our own code, so no extra library is needed.
//
//  Serial commands (115200 baud, Serial Monitor "No line ending" or any):
//    c = calibrate (clear air)   d = demo mode on/off   p = next LCD page
//    i = info (model + calibration)   h = help
// ============================================================================
#include <EEPROM.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <math.h>
#include "config.h"
#include "model_weights.h"
#include "calibration.h"

#define FW_VERSION "1.0.0"

LiquidCrystal_I2C lcd(LCD_I2C_ADDR, 16, 2);

enum { ST_CLEAR = 0, ST_LIGHT = 1, ST_DENSE = 2, MODE_FAULT = 3 };
static const char *const STATE_NAME[4] = {"CLEAR", "LIGHT FOG", "DENSE FOG", "FAULT"};
static const char *const FEATURE_NAME[MODEL_N_FEATURES] = {"temp", "humidity", "dew_spread", "light", "attenuation"};

#define FAULT_DHT   1
#define FAULT_LASER 2

// ---------------- Calibration stored in EEPROM ------------------------------
struct CalData {
  uint16_t magic;
  float baseline;  // laser signal (ADC counts) in clear air
};
#define CAL_MAGIC 0xF06A

// ---------------- Runtime settings (EEPROM, changed from the dashboard) -----
// config.h holds the defaults; "!set key=value" over USB serial overrides them.
struct Cfg {
  uint16_t magic;
  uint8_t brightLight, brightDense, nightPct, nightScale, confirmUp, confirmDown;
  uint16_t stepLight, stepDense;
};
#define CFG_MAGIC 0xF06B
#define CFG_ADDR  16  // CalData lives at 0
Cfg cfg;

// ---------------- Measurements & decision -----------------------------------
float tempC = NAN, humRH = NAN, dewSpread = NAN;
uint8_t dhtFails = 0;
int lightRaw = 0;
float laserSig = 0, baseline = 0;
bool calibrated = false;
bool rxSat = false;  // last laser measurement had a saturated receiver
float feat[MODEL_N_FEATURES];
float prob[MODEL_N_CLASSES];
uint8_t pred = ST_CLEAR, state = ST_CLEAR, pending = ST_CLEAR, pendingCount = 0;
uint8_t faults = 0, laserBadCount = 0;

// ---------------- Output / UI state -----------------------------------------
bool demo = false;
uint8_t demoMode = 0;
uint32_t demoSince = 0;
uint8_t chasePos = 0;
uint32_t lastStep = 0;
uint16_t shownPattern = 0xFFFF;
uint8_t lcdPage = 0;
uint32_t lastPageFlip = 0, manualUntil = 0;
char msg0[17] = "", msg1[17] = "";
uint32_t msgUntil = 0;

struct Button {
  uint8_t pin;
  bool down;
  bool longFired;
  uint32_t since;
};
Button btnCal = {PIN_BTN_CAL, false, false, 0};
Button btnPage = {PIN_BTN_PAGE, false, false, 0};

// ============================================================================
//  Helpers
// ============================================================================
float clampf(float x, float lo, float hi) { return x < lo ? lo : (x > hi ? hi : x); }

void lcdLine(uint8_t row, const char *s) {
  lcd.setCursor(0, row);
  uint8_t i = 0;
  for (; s[i] && i < 16; i++) lcd.write(s[i]);
  for (; i < 16; i++) lcd.write(' ');
}

void showMessage(const char *l0, const char *l1, uint16_t ms) {
  strncpy(msg0, l0, 16); msg0[16] = 0;
  strncpy(msg1, l1, 16); msg1[16] = 0;
  msgUntil = millis() + ms;
  lcdLine(0, msg0);
  lcdLine(1, msg1);
}

// Tenths as text, e.g. 24.3 -> "24.3", -1.5 -> "-1.5"
void fmt1(char *out, float v) {
  int t = (int)lroundf(v * 10);
  const char *sign = (t < 0) ? "-" : "";
  if (t < 0) t = -t;
  sprintf(out, "%s%d.%d", sign, t / 10, t % 10);
}

// ============================================================================
//  DHT11 (own driver, ~25 ms, blocking)
// ============================================================================
bool waitLevel(uint8_t level, uint16_t timeoutUs) {
  uint32_t start = micros();
  while (digitalRead(PIN_DHT) != level)
    if (micros() - start > timeoutUs) return false;
  return true;
}

bool readDHT11(float &t, float &h) {
  uint8_t d[5] = {0, 0, 0, 0, 0};
  pinMode(PIN_DHT, OUTPUT);
  digitalWrite(PIN_DHT, LOW);
  delay(20);  // start signal: at least 18 ms low
  pinMode(PIN_DHT, INPUT_PULLUP);
  // Sensor answers: 80 us low, 80 us high, then 40 bits
  if (!waitLevel(LOW, 200) || !waitLevel(HIGH, 150) || !waitLevel(LOW, 150)) return false;
  for (uint8_t i = 0; i < 40; i++) {
    if (!waitLevel(HIGH, 120)) return false;  // ~50 us low before every bit
    uint32_t start = micros();
    if (!waitLevel(LOW, 150)) return false;   // high: ~27 us = 0, ~70 us = 1
    if (micros() - start > 45) d[i / 8] |= 0x80 >> (i % 8);
  }
  if ((uint8_t)(d[0] + d[1] + d[2] + d[3]) != d[4]) return false;
  if (d[0] == 0 || d[0] > 100) return false;
  h = d[0] + d[1] * 0.1f;
  t = d[2] + (d[3] & 0x7F) * 0.1f;
  if (d[3] & 0x80) t = -t;
  return true;
}

// Magnus formula (Alduchov & Eskridge coefficients)
float dewPoint(float t, float rh) {
  rh = clampf(rh, 1, 100);
  const float b = 17.62f, c = 243.12f;
  float g = logf(rh / 100.0f) + b * t / (c + t);
  return c * g / (b - g);
}

// ============================================================================
//  Laser transmission sensor
// ============================================================================
int readAvg(uint8_t pin, uint8_t n) {
  long s = 0;
  for (uint8_t i = 0; i < n; i++) s += analogRead(pin);
  return (int)(s / n);
}

// Signal = reading with laser ON minus reading with laser OFF.
// Ambient light appears in both readings and cancels out.
// A cycle with a saturated receiver is discarded (the difference would be
// too small and look like fog); NAN = every cycle was saturated.
float measureLaser() {
  long total = 0;
  uint8_t valid = 0;
  for (uint8_t k = 0; k < LASER_CYCLES; k++) {
    digitalWrite(PIN_LASER, LOW);
    delay(LASER_SETTLE_MS);
    int off = readAvg(PIN_RX, 8);
    digitalWrite(PIN_LASER, HIGH);
    delay(LASER_SETTLE_MS);
    int on = readAvg(PIN_RX, 8);
    if (off >= RX_SAT_ADC || on >= RX_SAT_ADC) continue;
    total += on - off;
    valid++;
  }
  digitalWrite(PIN_LASER, LOW);
  return valid ? (float)total / valid : NAN;
}

void loadCalibration() {
  CalData cd;
  EEPROM.get(0, cd);
  calibrated = (cd.magic == CAL_MAGIC && cd.baseline >= CAL_MIN_SIGNAL && cd.baseline < 1024);
  baseline = calibrated ? cd.baseline : 0;
}

void cfgDefaults() {
  cfg.magic = CFG_MAGIC;
  cfg.brightLight = BRIGHT_LIGHT;
  cfg.brightDense = BRIGHT_DENSE;
  cfg.nightPct = (uint8_t)(NIGHT_LIGHT * 100 + 0.5);
  cfg.nightScale = NIGHT_SCALE;
  cfg.confirmUp = CONFIRM_UP;
  cfg.confirmDown = CONFIRM_DOWN;
  cfg.stepLight = STEP_MS_LIGHT;
  cfg.stepDense = STEP_MS_DENSE;
}

void loadCfg() {
  Cfg c;
  EEPROM.get(CFG_ADDR, c);
  bool ok = c.magic == CFG_MAGIC && c.nightPct <= 100 && c.nightScale <= 100 &&
            c.confirmUp >= 1 && c.confirmUp <= 20 && c.confirmDown >= 1 && c.confirmDown <= 20 &&
            c.stepLight >= 50 && c.stepLight <= 1000 && c.stepDense >= 50 && c.stepDense <= 2000;
  if (ok) cfg = c; else cfgDefaults();
}

void printCfg() {
  Serial.print(F("C,brightLight=")); Serial.print(cfg.brightLight);
  Serial.print(F(",brightDense=")); Serial.print(cfg.brightDense);
  Serial.print(F(",nightPct=")); Serial.print(cfg.nightPct);
  Serial.print(F(",nightScale=")); Serial.print(cfg.nightScale);
  Serial.print(F(",confirmUp=")); Serial.print(cfg.confirmUp);
  Serial.print(F(",confirmDown=")); Serial.print(cfg.confirmDown);
  Serial.print(F(",stepLight=")); Serial.print(cfg.stepLight);
  Serial.print(F(",stepDense=")); Serial.println(cfg.stepDense);
}

// Calibration compiled into this firmware (calibration.h), so the dashboard can compare it with the file on disk.
void printCal() {
  Serial.print(F("K,ldrLo=")); Serial.print(CAL_LDR_LO);
  Serial.print(F(",ldrHi=")); Serial.print(CAL_LDR_HI);
  Serial.print(F(",tGain=")); Serial.print(CAL_TEMP_GAIN, 4);
  Serial.print(F(",tOff=")); Serial.print(CAL_TEMP_OFFSET, 2);
  Serial.print(F(",hGain=")); Serial.print(CAL_HUM_GAIN, 4);
  Serial.print(F(",hOff=")); Serial.println(CAL_HUM_OFFSET, 2);
}

// Values are clamped to the same limits the dashboard shows.
bool setCfg(const char *k, long v) {
  if (!strcmp(k, "brightLight")) cfg.brightLight = constrain(v, 0, 255);
  else if (!strcmp(k, "brightDense")) cfg.brightDense = constrain(v, 0, 255);
  else if (!strcmp(k, "nightPct")) cfg.nightPct = constrain(v, 0, 100);
  else if (!strcmp(k, "nightScale")) cfg.nightScale = constrain(v, 0, 100);
  else if (!strcmp(k, "confirmUp")) cfg.confirmUp = constrain(v, 1, 20);
  else if (!strcmp(k, "confirmDown")) cfg.confirmDown = constrain(v, 1, 20);
  else if (!strcmp(k, "stepLight")) cfg.stepLight = constrain(v, 50, 1000);
  else if (!strcmp(k, "stepDense")) cfg.stepDense = constrain(v, 50, 2000);
  else return false;
  return true;
}

void calibrate() {
  showMessage("Calibrating...", "Keep air clear", 60000);
  writePattern(0, 0);
  float s = 0, lo = 1e9, hi = -1e9;
  for (uint8_t i = 0; i < 8; i++) {
    float v = measureLaser();
    if (isnan(v)) {
      Serial.println(F("# CAL FAILED: receiver saturated. Shade the receiver or lower its load resistor."));
      showMessage("CAL FAILED", "Rx saturated", 4000);
      return;
    }
    s += v;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  s /= 8;
  char b[17];
  if (s < CAL_MIN_SIGNAL) {
    Serial.print(F("# CAL FAILED: laser signal ")); Serial.print(s, 1);
    Serial.println(F(" too low. Align laser with the receiver."));
    sprintf(b, "Signal %d low", (int)s);
    showMessage("CAL FAILED", b, 4000);
    return;
  }
  // Clear-air check: a baseline taken in fog would make fog look like clear air.
  if (!isnan(humRH) && humRH > CAL_MAX_HUM) {
    Serial.print(F("# CAL FAILED: humidity ")); Serial.print(humRH, 1);
    Serial.println(F(" % - air is not clear. Calibrate in clear air."));
    showMessage("CAL FAILED", "Air not clear", 4000);
    return;
  }
  if (hi - lo > s * CAL_MAX_SPREAD) {
    Serial.println(F("# CAL FAILED: laser signal unstable (something in the beam?). Try again."));
    showMessage("CAL FAILED", "Signal unstable", 4000);
    return;
  }
  if (isnan(humRH)) Serial.println(F("# CAL WARNING: no DHT11 reading, clear air not verified."));
  baseline = s;
  calibrated = true;
  CalData cd = {CAL_MAGIC, baseline};
  EEPROM.put(0, cd);
  laserBadCount = 0;
  faults &= ~FAULT_LASER;
  Serial.print(F("# CAL OK: baseline = ")); Serial.println(baseline, 1);
  sprintf(b, "Baseline %d", (int)baseline);
  showMessage("CAL OK", b, 2500);
}

// ============================================================================
//  Features + edge AI
// ============================================================================
void computeFeatures() {
  float t = isnan(tempC) ? 25.0f : tempC;
  float h = isnan(humRH) ? 50.0f : humRH;
  dewSpread = t - dewPoint(t, h);
  feat[0] = clampf((t - 25.0f) / 10.0f, -2, 3);
  feat[1] = clampf((h - 70.0f) / 20.0f, -3.5f, 1.5f);
  feat[2] = clampf(dewSpread / 5.0f, 0, 4);
  feat[3] = clampf((lightRaw - CAL_LDR_LO) / (float)(CAL_LDR_HI - CAL_LDR_LO), 0, 1);
  feat[4] = calibrated ? clampf(1.0f - laserSig / baseline, -0.2f, 1.2f) : 0.0f;
}

void runModel() {
  float z[MODEL_N_CLASSES], zmax = -1e9;
  for (uint8_t k = 0; k < MODEL_N_CLASSES; k++) {
    z[k] = MODEL_B[k];
    for (uint8_t i = 0; i < MODEL_N_FEATURES; i++) z[k] += MODEL_W[k][i] * feat[i];
    if (z[k] > zmax) zmax = z[k];
  }
  float sum = 0;
  for (uint8_t k = 0; k < MODEL_N_CLASSES; k++) { prob[k] = expf(z[k] - zmax); sum += prob[k]; }
  pred = 0;
  for (uint8_t k = 0; k < MODEL_N_CLASSES; k++) {
    prob[k] /= sum;
    if (prob[k] > prob[pred]) pred = k;
  }
}

void checkFaults() {
  if (dhtFails >= DHT_MAX_FAILS) faults |= FAULT_DHT; else faults &= ~FAULT_DHT;
  // A (nearly) vanished laser beam while the air is dry cannot be fog:
  // the beam is blocked or misaligned.
  // The humidity must be a fresh reading: a stale one (DHT11 failing in fog)
  // must not turn a dense-fog beam loss into a "laser fault".
  bool bad = calibrated && laserSig < baseline * LASER_FAULT_FRAC &&
             dhtFails == 0 && !isnan(humRH) && humRH < LASER_FAULT_MAX_H;
  laserBadCount = bad ? min(laserBadCount + 1, 250) : 0;
  if (laserBadCount >= FAULT_CONFIRM) faults |= FAULT_LASER; else faults &= ~FAULT_LASER;
}

// Hysteresis: become more severe quickly, relax slowly.
void updateState() {
  if (pred == state) { pendingCount = 0; return; }
  if (pred != pending) { pending = pred; pendingCount = 0; }
  pendingCount++;
  uint8_t need = pred > state ? cfg.confirmUp : cfg.confirmDown;
  if (pendingCount >= need) {
    Serial.print(F("# STATE ")); Serial.print(STATE_NAME[state]);
    Serial.print(F(" -> ")); Serial.println(STATE_NAME[pred]);
    state = pred;
    pendingCount = 0;
  }
}

void printHeader() {
  Serial.println(F("H,ms,temp,hum,dew_spread,light_raw,laser_sig,baseline,"
                   "f_temp,f_humidity,f_dew_spread,f_light,f_attenuation,"
                   "p_clear,p_light,p_dense,pred,state,fault,demo"));
}

void printSample() {
  Serial.print(F("D,")); Serial.print(millis()); Serial.print(',');
  Serial.print(tempC, 1); Serial.print(',');
  Serial.print(humRH, 1); Serial.print(',');
  Serial.print(dewSpread, 2); Serial.print(',');
  Serial.print(lightRaw); Serial.print(',');
  Serial.print(laserSig, 1); Serial.print(',');
  Serial.print(baseline, 1);
  for (uint8_t i = 0; i < MODEL_N_FEATURES; i++) { Serial.print(','); Serial.print(feat[i], 4); }
  for (uint8_t k = 0; k < MODEL_N_CLASSES; k++) { Serial.print(','); Serial.print(prob[k], 3); }
  Serial.print(','); Serial.print(pred);
  Serial.print(','); Serial.print(state);
  Serial.print(','); Serial.print(faults);
  Serial.print(','); Serial.println(demo ? 1 : 0);
}

uint8_t currentMode();
uint8_t brightnessFor(uint8_t mode);

// Extra line for the dashboard (the recorder ignores it): S,mode,bright,calibrated,dhtFails,rxSat
void printStatus() {
  uint8_t mode = currentMode();
  Serial.print(F("S,")); Serial.print(mode);
  Serial.print(','); Serial.print(brightnessFor(mode));
  Serial.print(','); Serial.print(calibrated ? 1 : 0);
  Serial.print(','); Serial.print(dhtFails);
  Serial.print(','); Serial.println(rxSat ? 1 : 0);
}

// Raw line (the recorder and train_model.py ignore it): R,ms,dhtTemp,dhtHum,ldr,rxOff,rxOn
// Unfiltered values: DHT11 as read (before gain/offset), one single analogRead of the LDR and of
// the receiver with the laser OFF and ON (no averaging, no OFF/ON subtraction). nan = DHT11 read failed.
float rawT = NAN, rawH = NAN;

void printRaw() {
  int ldr = analogRead(PIN_LDR);
  digitalWrite(PIN_LASER, LOW);
  delay(LASER_SETTLE_MS);
  int off = analogRead(PIN_RX);
  digitalWrite(PIN_LASER, HIGH);
  delay(LASER_SETTLE_MS);
  int on = analogRead(PIN_RX);
  digitalWrite(PIN_LASER, LOW);
  Serial.print(F("R,")); Serial.print(millis());
  Serial.print(','); Serial.print(rawT, 1);
  Serial.print(','); Serial.print(rawH, 1);
  Serial.print(','); Serial.print(ldr);
  Serial.print(','); Serial.print(off);
  Serial.print(','); Serial.println(on);
}

void sampleAndDecide(uint32_t now) {
  static uint32_t lastDht = 0;
  static bool first = true;
  if (first || now - lastDht >= DHT_EVERY_MS) {
    first = false;
    lastDht = now;
    float t, h;
    if (readDHT11(t, h)) {
      rawT = t; rawH = h;
      tempC = t * CAL_TEMP_GAIN + CAL_TEMP_OFFSET;
      humRH = clampf(h * CAL_HUM_GAIN + CAL_HUM_OFFSET, 1, 100);
      dhtFails = 0;
    }
    else {
      rawT = rawH = NAN;
      if (dhtFails < 250) dhtFails++;
    }
  }
  lightRaw = readAvg(PIN_LDR, 8);
  float ls = measureLaser();
  rxSat = isnan(ls);
  if (!rxSat) laserSig = ls;  // saturated receiver: keep the last valid value
  computeFeatures();
  runModel();
  checkFaults();
  updateState();
  printSample();
  printStatus();
  printRaw();
}

// ============================================================================
//  Guiding lights (74HC595) + status LED
// ============================================================================
void writePattern(uint16_t pattern, uint8_t bright) {
  if (pattern != shownPattern) {
    digitalWrite(PIN_595_LATCH, LOW);
    // The first byte sent ends up in the LAST chip of the chain.
    for (int8_t chip = NUM_SHIFT_REGS - 1; chip >= 0; chip--) shiftOut(PIN_595_DATA, PIN_595_CLOCK, MSBFIRST, (uint8_t)(pattern >> (8 * chip)));
    digitalWrite(PIN_595_LATCH, HIGH);
    shownPattern = pattern;
  }
  (void)bright;  // no OE/PWM line any more: LEDs are fully on or off
}

// Fail-safe: a lost DHT11 (common in fog) is only fatal if the laser cannot
// cover for it; without calibration the lights never drop below LIGHT FOG.
uint8_t currentMode() {
  if (demo) return demoMode;
  if ((faults & FAULT_LASER) || ((faults & FAULT_DHT) && !calibrated)) return MODE_FAULT;
  if (!calibrated && state < ST_LIGHT) return ST_LIGHT;
  return state;
}

uint8_t brightnessFor(uint8_t mode) {
  if (mode == ST_CLEAR) return 0;
  if (mode == MODE_FAULT) return 255;
  uint16_t b = (mode == ST_DENSE) ? cfg.brightDense : cfg.brightLight;
  if (feat[3] < cfg.nightPct / 100.0f) b = b * cfg.nightScale / 100;
  return (uint8_t)b;
}

void updateLights(uint32_t now) {
  uint8_t mode = currentMode();
  uint16_t pattern = 0;
  if (mode == MODE_FAULT) {
    if ((now / FAULT_BLINK_MS) % 2 == 0) pattern = (1UL << NUM_LEDS) - 1;
  } else if (mode != ST_CLEAR) {
    uint16_t stepMs = (mode == ST_DENSE) ? cfg.stepDense : cfg.stepLight;
    if (now - lastStep >= stepMs) { lastStep = now; chasePos = (chasePos + 1) % NUM_LEDS; }
    for (uint8_t g = 0; g < CHASE_GROUP; g++) pattern |= 1U << ((chasePos + NUM_LEDS - g) % NUM_LEDS);
  }
  writePattern(pattern, brightnessFor(mode));

  bool on;
  if (faults && !demo) on = (now / 150) % 2;
  else if (!calibrated) on = (now / 1000) % 2;
  else on = mode != ST_CLEAR;
  digitalWrite(PIN_STATUS, on);
}

// ============================================================================
//  LCD pages
// ============================================================================
void drawLcd(uint32_t now) {
  if (now < msgUntil) return;
  char l0[24], l1[24], a[8], b[8];
  uint8_t mode = currentMode();

  if (mode == MODE_FAULT) {
    sprintf(l0, "%s", demo ? "DEMO: FAULT" : "!! SENSOR FAULT");
    const char *what = demo ? "TEST" : (faults == (FAULT_DHT | FAULT_LASER) ? "DHT+LASR" : (faults & FAULT_DHT) ? "DHT11" : "LASER");
    sprintf(l1, "%s FAILSAFE", what);
    lcdLine(0, l0); lcdLine(1, l1);
    return;
  }

  switch (lcdPage) {
    case 0: {
      if (demo) sprintf(l0, "DEMO:%s", STATE_NAME[mode]);
      else sprintf(l0, "%-10s %3d%%", STATE_NAME[state], (int)lroundf(prob[state] * 100));
      if (!calibrated && (now / 2000) % 2) sprintf(l1, "NOT CALIBRATED");
      else if (mode == ST_CLEAR) sprintf(l1, "Guide: OFF");
      else if (mode == ST_LIGHT) sprintf(l1, "Guide: NORMAL");
      else sprintf(l1, "Guide: SLOW DOWN");
      break;
    }
    case 1: {
      if (isnan(tempC)) { sprintf(l0, "T:--.-C H:--%%"); sprintf(l1, "DewSprd: --.-C"); break; }
      fmt1(a, tempC);
      sprintf(l0, "T:%sC H:%d%%", a, (int)lroundf(humRH));
      fmt1(b, dewSpread);
      sprintf(l1, "DewSprd: %sC", b);
      break;
    }
    case 2: {
      if (calibrated) sprintf(l0, "Laser att:%4d%%", (int)lroundf(feat[4] * 100));
      else sprintf(l0, "Laser: NO CAL");
      sprintf(l1, "Light:%3d%% S:%d", (int)lroundf(feat[3] * 100), (int)laserSig);
      break;
    }
    default: {
      sprintf(l0, "AI  C   L   D  %%");
      sprintf(l1, "   %3d %3d %3d", (int)lroundf(prob[0] * 100), (int)lroundf(prob[1] * 100), (int)lroundf(prob[2] * 100));
      break;
    }
  }
  lcdLine(0, l0);
  lcdLine(1, l1);
}

void nextPage(uint32_t now) {
  lcdPage = (lcdPage + 1) % 4;
  manualUntil = now + LCD_MANUAL_MS;
  lastPageFlip = now;
  msgUntil = 0;
  drawLcd(now);
}

// ============================================================================
//  Buttons & serial commands
// ============================================================================
// 0 = nothing, 1 = short press (on release), 2 = long press (while held)
uint8_t pollButton(Button &b, uint32_t now) {
  bool pressed = digitalRead(b.pin) == LOW;
  if (pressed && !b.down) { b.down = true; b.longFired = false; b.since = now; return 0; }
  if (pressed && !b.longFired && now - b.since >= LONG_PRESS_MS) { b.longFired = true; return 2; }
  if (!pressed && b.down) {
    b.down = false;
    if (!b.longFired && now - b.since >= 30) return 1;
  }
  return 0;
}

void toggleDemo(uint32_t now) {
  demo = !demo;
  demoMode = ST_CLEAR;
  demoSince = now;
  Serial.println(demo ? F("# DEMO ON") : F("# DEMO OFF"));
  showMessage(demo ? "DEMO MODE ON" : "DEMO MODE OFF", demo ? "Cycling states" : "Back to sensors", 1500);
}

void printInfo() {
  Serial.println(F("# Fog Illumination Uno v" FW_VERSION));
  Serial.print(F("# calibrated=")); Serial.print(calibrated ? F("yes") : F("no"));
  Serial.print(F(" baseline=")); Serial.println(baseline, 1);
  for (uint8_t k = 0; k < MODEL_N_CLASSES; k++) {
    Serial.print(F("# W[")); Serial.print(STATE_NAME[k]); Serial.print(F("] b="));
    Serial.print(MODEL_B[k], 3);
    for (uint8_t i = 0; i < MODEL_N_FEATURES; i++) {
      Serial.print(' '); Serial.print(FEATURE_NAME[i]); Serial.print('=');
      Serial.print(MODEL_W[k][i], 3);
    }
    Serial.println();
  }
}

void printHelp() {
  Serial.println(F("# Commands: c=calibrate  d=demo on/off  p=next LCD page  i=info  h=help"));
  Serial.println(F("# Settings: !get  !reset  !set key=value  (see README.id.md)"));
}

// Line commands start with '!' (single letters below keep working):
//   !get   !reset   !set key=value
void handleLine(char *s) {
  if (!strcmp(s, "get")) { printCfg(); printCal(); return; }
  if (!strcmp(s, "reset")) { cfgDefaults(); EEPROM.put(CFG_ADDR, cfg); printCfg(); return; }
  if (!strncmp(s, "set ", 4)) {
    char *eq = strchr(s + 4, '=');
    if (eq) {
      *eq = 0;
      if (setCfg(s + 4, atol(eq + 1))) { EEPROM.put(CFG_ADDR, cfg); printCfg(); return; }
    }
  }
  Serial.println(F("# ERR bad line command (!get, !reset, !set key=value)"));
}

void handleSerial(uint32_t now) {
  static char line[40];
  static uint8_t len = 0;
  static bool inLine = false;
  while (Serial.available()) {
    char c = Serial.read();
    if (inLine) {
      if (c == '\n' || c == '\r') { line[len] = 0; inLine = false; if (len) handleLine(line); }
      else if (len < sizeof(line) - 1) line[len++] = c;
      continue;
    }
    if (c == '!') { inLine = true; len = 0; continue; }
    if (c == 'c' || c == 'C') calibrate();
    else if (c == 'd' || c == 'D') toggleDemo(now);
    else if (c == 'p' || c == 'P') nextPage(now);
    else if (c == 'i' || c == 'I') printInfo();
    else if (c == 'h' || c == 'H' || c == '?') printHelp();
  }
}

// ============================================================================
//  Setup & loop
// ============================================================================
void setup() {
  pinMode(PIN_595_LATCH, OUTPUT);
  pinMode(PIN_595_DATA, OUTPUT);
  pinMode(PIN_595_CLOCK, OUTPUT);
  pinMode(PIN_LASER, OUTPUT);
  digitalWrite(PIN_LASER, LOW);
  pinMode(PIN_STATUS, OUTPUT);
  pinMode(PIN_BTN_CAL, INPUT_PULLUP);
  pinMode(PIN_BTN_PAGE, INPUT_PULLUP);
  pinMode(PIN_DHT, INPUT_PULLUP);
  writePattern(0, 0);

  Serial.begin(SERIAL_BAUD);
  lcd.init();
  lcd.backlight();
  showMessage("Fog Lane Light", "v" FW_VERSION " booting", 1500);

  // Lamp test: sweep every LED once
  for (uint8_t i = 0; i < NUM_LEDS; i++) { writePattern(1U << i, 255); delay(60); }
  writePattern(0, 0);

  loadCalibration();
  loadCfg();
  Serial.println(F("# Fog Illumination Uno v" FW_VERSION));
  printHelp();
  printCfg();
  printCal();
  if (!calibrated) {
    Serial.println(F("# Not calibrated: in clear air hold the CAL button 2 s or send 'c'"));
    showMessage("NOT CALIBRATED", "Hold CAL 2s", 2500);
  } else {
    Serial.print(F("# Calibration baseline = ")); Serial.println(baseline, 1);
  }
  printHeader();
}

void loop() {
  uint32_t now = millis();
  static uint32_t lastSample = 0, lastLcd = 0;
  static bool started = false;

  handleSerial(now);

  uint8_t e = pollButton(btnCal, now);
  if (e == 2) calibrate();
  else if (e == 1) showMessage("Hold CAL button", "2 s to calibrate", 1500);

  e = pollButton(btnPage, now);
  if (e == 1) nextPage(now);
  else if (e == 2) toggleDemo(now);

  if (!started || now - lastSample >= SAMPLE_MS) {
    started = true;
    lastSample = now;
    sampleAndDecide(now);
  }

  if (demo && now - demoSince >= DEMO_STEP_MS) {
    demoSince = now;
    demoMode = (demoMode + 1) % 4;
  }

  updateLights(now);

  if (now > manualUntil && now - lastPageFlip >= LCD_PAGE_MS) {
    lastPageFlip = now;
    lcdPage = (lcdPage + 1) % 4;
  }
  if (now - lastLcd >= LCD_REFRESH_MS) {
    lastLcd = now;
    drawLcd(now);
  }
}
