#pragma once
// ============================================================================
//  Fog-Resilient Smart Road Lane Illumination — configuration (Arduino Uno)
//  Change wiring / timing / thresholds here, then re-upload.
// ============================================================================

// ---------------- Pin map (must match the wiring diagram) -------------------
// D0/D1 are the USB serial port: leave them free (used for AI data recording).
#define PIN_DHT        2    // DHT11 data (10k pull-up to 5V if using a bare sensor)

// LCD 16x2 on an I2C backpack (PCF8574): SDA = A4, SCL = A5 (fixed hardware pins on the Uno).
#define LCD_I2C_ADDR   0x27 // try 0x3F if the screen stays blank

// 74HC595 (bit-banged with shiftOut, OE pin 13 tied to GND = LEDs always enabled, no brightness PWM)
#define PIN_595_CLOCK  13   // 74HC595 pin 11 (SH_CP)
#define PIN_595_LATCH  12   // 74HC595 pin 12 (ST_CP)
#define PIN_595_DATA   11   // 74HC595 pin 14 (DS)

#define PIN_LDR        A0   // LDR from 5V to A0, 10k from A0 to GND  (brighter = higher)
#define PIN_RX         A1   // Phototransistor: collector 5V, emitter A1, 10k to GND
#define PIN_LASER      A2   // Laser module through NPN transistor (HIGH = laser on)
#define PIN_BTN_CAL    3    // Push button to GND (internal pull-up). Hold 2 s = calibrate
#define PIN_BTN_PAGE   4    // Push button to GND. Short = next LCD page, hold 2 s = demo mode
#define PIN_STATUS     5    // Status / fault LED (+330 ohm)

// ---------------- Guiding lights (74HC595) ---------------------------------
#define NUM_SHIFT_REGS   1  // 1 chip = 8 LEDs, 2 chained chips = 16 LEDs
#define NUM_LEDS         8  // LEDs actually fitted (6..8 per chip). LED1 = Q0
#define CHASE_GROUP      3  // how many neighbouring LEDs are lit together
#define STEP_MS_LIGHT  150  // chase step in LIGHT FOG (normal speed)
#define STEP_MS_DENSE  300  // chase step in DENSE FOG (slower = "slow down")
#define FAULT_BLINK_MS 400  // all LEDs blink together when a sensor has failed

// Nominal brightness 0..255: only reported in telemetry, NOT applied to the LEDs (no OE/PWM line)
#define BRIGHT_LIGHT   150
#define BRIGHT_DENSE   255
#define NIGHT_LIGHT   0.20  // ambient light feature below this = night
#define NIGHT_SCALE     60  // % brightness at night (less glare in fog)

// ---------------- Sampling --------------------------------------------------
#define SAMPLE_MS        1000  // sensors + AI decision + CSV line
#define DHT_EVERY_MS     2000  // DHT11 must not be read faster than 1 Hz
#define LASER_CYCLES        4  // laser OFF/ON pairs averaged per sample
#define LASER_SETTLE_MS     3  // wait after switching the laser before reading
#define SERIAL_BAUD    115200

// ---------------- Decision / state machine ---------------------------------
#define CONFIRM_UP     2   // samples in a row needed to go to a MORE severe state
#define CONFIRM_DOWN   5   // samples in a row needed to go to a LESS severe state

// ---------------- Calibration & fault detection -----------------------------
#define CAL_MIN_SIGNAL     40.0  // laser signal (ADC counts) below this = not aligned
#define LASER_FAULT_FRAC   0.05  // signal < 5 % of baseline ...
#define LASER_FAULT_MAX_H  85.0  // ... while humidity is below 85 % cannot be fog -> fault
#define RX_SAT_ADC         1018  // receiver reading at/above this = ADC saturated, laser signal invalid
#define CAL_MAX_HUM        85.0  // calibration refused above this humidity (air not clear)
#define CAL_MAX_SPREAD     0.10  // calibration refused if the 8 readings vary more than 10 % of their mean
#define FAULT_CONFIRM      3    // bad samples in a row before a fault is raised
#define DHT_MAX_FAILS      3     // failed DHT reads in a row before a fault is raised

// ---------------- User interface --------------------------------------------
#define LONG_PRESS_MS    2000
#define LCD_REFRESH_MS    500
#define LCD_PAGE_MS      3000  // automatic page rotation
#define LCD_MANUAL_MS   15000  // after a button press, stay on that page this long
#define DEMO_STEP_MS     5000  // demo mode: time per state
