# Smart Fog Detection and Lane Illumination (Arduino Uno)

A fog-resilient road-lane lighting prototype. An Arduino Uno measures fog with three cheap sensors, an
on-board edge-AI model classifies the air as **CLEAR / LIGHT FOG / DENSE FOG**, and eight guide lights
(through a 74HC595) switch on and chase at a speed that depends on the fog density.
If a sensor fails the system falls back to a fail-safe so the road is never left dark.

> Full build and usage guide (wiring, calibration, recording data, training): **[README.id.md](README.id.md)** (Indonesian).

![Block diagram](docs/block_diagram.png)

## How it works
- **Sensors:** DHT11 (temperature and humidity, giving the dew-point spread), LDR (ambient light, night dimming) and a
  laser + phototransistor path whose attenuation is measured with the laser switched ON and OFF so sunlight cancels out.
- **Decision:** softmax logistic regression on five features, run directly on the Uno, plus hysteresis (fast up, slow down)
  and fail-safe rules for DHT11 and laser faults. Weights are trained from your own recordings (`tools/train_model.py`).
- **Output:** 74HC595 chasing lights (on/off, no PWM), 16x2 LCD, status LED and a CSV telemetry stream over USB.

## Repository layout
| Path | What it is |
|---|---|
| `firmware/fog_illumination_uno/` | Uno firmware (`config.h` holds pins, timing and thresholds, `model_weights.h` the AI weights) |
| `tools/` | Serial recorder, model training, stdlib serial-port access (no `pip install`) |
| `dashboard/` | PC dashboard: live view, settings over USB serial, recording, simulator, USB-serial driver helper |
| `docs/` | Wiring, block and flow diagrams |
| `START_DASHBOARD.bat`, `RECORD_DATA.bat`, `TRAIN_MODEL.bat` | One-click launchers (Windows, Python 3) |

## Quick start
1. Wire the board as in `docs/wiring_diagram.png` and upload `firmware/fog_illumination_uno` with the Arduino IDE
   (board *Arduino Uno*; only built-in libraries are needed).
2. Calibrate the laser once in **clear air** (hold the CAL button for 2 s, or send `c` over serial).
3. Run `START_DASHBOARD.bat` and open `http://localhost:5200`. No Arduino at hand? Use the built-in simulator
   (`python dashboard/server.py --sim --open`).

The dashboard monitors everything (fog state and probabilities, the five features, laser attenuation, lights, faults,
calibration) and sends commands to the board over USB serial: calibrate, demo mode, and settings such as chase speed
and confirmation times (stored in the Uno's EEPROM).

## Code graph
An interactive knowledge graph of the firmware, tools, dashboard and docs (built with [graphify](https://github.com/Graphify-Labs/graphify);
functions, files, concepts and how they connect, grouped into communities):
**https://skillshub-electronics.github.io/smart-fog-detector-and-illumination-v1/graph/**
(the page is `docs/graph/index.html`; to serve it yourself enable GitHub Pages: *Settings > Pages > Deploy from branch > `main` / `/docs`*).

## Status
Firmware compiles for `arduino:avr:uno` (55 % flash, 60 % RAM) and the serial link and dashboard were tested on a real
Uno (CH340 clone). Sensor wiring still has to be verified on each build: DHT11 and laser readings were not valid yet in the
first hardware test.

The project is a table-top prototype, not a certified road device.
