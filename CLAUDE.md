# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Aturan proyek (berlaku untuk semua sesi)

Semua pekerjaan di proyek ini, dari sesi mana pun, wajib:

1. Mengikuti pedoman Karpathy di atas (nyatakan asumsi dan tanya bila tidak jelas, perubahan minimal, jangan sentuh kode yang tidak diminta, verifikasi tiap perubahan).
2. Memakai graphify: bila `graphify-out/` ada, jawab pertanyaan tentang proyek lewat `graphify query` lebih dulu, dan jalankan `graphify update .` setelah mengubah kode.
3. Menyebutkan di laporan akhir bahwa kedua aturan ini diikuti.

## Dashboard (PC) - catatan untuk sesi berikutnya
- `START_DASHBOARD.bat` -> `dashboard/server.py` (stdlib, port 5200, hanya localhost) + `dashboard/static/index.html`; `dashboard/simulator.py` = kembaran firmware.
  Gaya halaman meniru `..\Camel Pedestrian` (folder itu tidak boleh diubah dari sini).
- Server memakai `tools/serial_port.py` dan membaca baris `H,`/`D,`/`S,`/`C,`/`#`. Hanya satu program per port COM (dashboard, `RECORD_DATA.bat`, Serial Monitor).
- **Firmware dan simulator harus tetap identik.** Jika logika `.ino` berubah (`computeFeatures`, `runModel`, `checkFaults`, `updateState`, `currentMode`,
  `brightnessFor`, kalibrasi, parser serial), ubah `dashboard/simulator.py` juga. Konstanta dibaca simulator dari `config.h`; bobot dari `model_weights.h`.
- Protokol tambahan (firmware): `!get`, `!set key=nilai`, `!reset` (pengaturan di EEPROM alamat 16, `Cfg`), baris status `S,mode,bright,calibrated,dhtFails,rxSat`,
  baris `C,...`. Kolom `D,` tidak diubah (recorder dan `train_model.py` bergantung padanya). Batas nilai: `CFG_LIMITS` di simulator = `setCfg()` di firmware.
- Rekaman simulator disimpan sebagai `data/sim_*.csv` agar tidak ikut training (`train_*.csv`).
- Firmware belum bisa dikompilasi di PC ini (tidak ada arduino-cli/g++); verifikasi dengan Arduino IDE. Simulator diuji, perilaku di Uno belum.
- **Driver USB-serial** (`dashboard/drivers.py`, kartu di Settings, `GET /api/drivers`, `POST /api/drivers/rescan|install`): deteksi lewat `Win32_PnPEntity`
  (VID 1A86 CH340, 10C4 CP210x, 0403 FTDI, 2341/2A03 Arduino; kode 28 = driver belum ada). `pnputil /scan-devices` dan instalasi butuh admin: selalu lewat UAC dan
  hanya atas klik pengguna; installer hanya dari folder `drivers/` (nama harus ada di daftar, tanpa path bebas). **Jangan** menambah unduhan/eksekusi otomatis dari internet.
  `server.py` menolak Host selain localhost dan Origin lintas situs (server bisa menggerakkan Uno dan installer) - pertahankan.
- **Tes hardware 2026-10-19 (COM3, Uno clone CH340):** firmware (dengan `!get/!set/!reset`, baris `S,`/`C,`) dikompilasi lewat arduino-cli bawaan Arduino IDE
  (`arduino:avr:uno`, build-path pendek karena path proyek panjang): flash 18008 B (55%), RAM 1243 B (60%), tanpa warning; diunggah ke COM3. Protokol serial dan dashboard
  terbukti jalan di Uno sungguhan. Temuan sensor: DHT11 tidak terbaca (`nan`, FAULT sebelum kalibrasi), sinyal laser ~10 count (< `CAL_MIN_SIGNAL` 40), LDR ~300/1023.
  Belum dikalibrasi.
- **Kalibrasi sensor** (`firmware/.../calibration.h`, halaman Calibration): `CAL_LDR_LO/HI`, `CAL_TEMP_GAIN/OFFSET`, `CAL_HUM_GAIN/OFFSET`. Dipakai di `computeFeatures()` (cahaya) dan saat
  pembacaan DHT11 (`sampleAndDecide()`); simulator membaca file yang sama (`_read_defines`) dan menerapkan rumus yang sama. Firmware mengirim baris `K,...` (nilai yang dikompilasi) saat boot dan
  setelah `!get`; server menyimpannya sebagai `calBoard` untuk membandingkan dengan file. `dashboard/maintenance.py` membatasi dan menulis file (batas: `CAL_FIELDS`), cadangan lama di `backups/calibration_*.h`.
  Nilai yang berubah menggeser fitur model: rekam ulang dan latih ulang.
- **Upload dan learning dari dashboard** (`dashboard/maintenance.py`): satu `JOB` sekali jalan (upload = `arduino-cli compile` + `upload`, port dilepas lalu disambung lagi; latih/instal =
  subproses `tools/train_model.py`). `model_weights.h` dicadangkan ke `backups/` sebelum `--install`; data `sim_*.csv` tidak pernah boleh di-install. Folder build di `%TEMP%\fog_build` (path pendek,
  path proyek terlalu panjang untuk toolchain).
- **Tes 2026-10-19 di Uno COM3:** upload dari dashboard berhasil (compile 18350 B / 56 %, RAM 1243 B / 60 %), board melaporkan baris `K,` dan dashboard tersambung ulang otomatis.

