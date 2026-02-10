# Project Notes (MSCL Configurator)

## Date
2026-02-06

## Goal
Stabilize MSCL Configurator for TC-Link-200 OEM nodes, improve base station stability, and make UI clearer.

## Key Changes (Current State)
- HTML template moved to `app/templates/mscl_web_config.html` and loaded from `mscl_config.py`.
- Removed auto status refresh to reduce base station ping storms.
- Combined “Refresh” with “Connect & Refresh” and removed top “Check USB” button.
- Added “Node Settings” header above editable parameters.
- Added “Node Status” block styled like “Node Diagnostics”.
- Added detailed logging for Read/Write attempts and errors.
- Suppressed Flask request logs (GET/POST) via werkzeug logger.
- Added OP-level lock to serialize all base station operations.
- Added double `setToIdle()` with longer delay before read/write.
- Added EEPROM backoff for read/write.
- Read now returns partial data even if `getSampleRate()` fails; Sample Rate shows `N/A` in UI.
- Battery removed from Node Status (OEM shows 0V).
- Node Status now includes: Model, FW, S/N, Region, Last Comm, State, Storage %, Sampling Mode, Data Mode.

## Known Issues
- Frequent EEPROM read errors: `Failed to read the Sampling Mode (EEPROM 24)` on some reads/writes.
- Base station ping failures still happen occasionally; manual reconnect helps.

## Suggested SensorConnect Settings (Stability)
- Sampling Mode: Sync
- Sample Rate: 1–4 Hz (start with 1 Hz)
- Data Collection Method: Continuous/Streaming (if available)
- Active Channels: only required
- Radio Power: 20 dBm (Max)
- Lost Beacon Timeout: 2–5 min
- Inactivity Timeout: 300–600 s
- Diagnostic Info Interval: 60–300 s
- Communication Protocol: keep default (usually 1)

## Files Touched
- `app/mscl_config.py`
- `app/templates/mscl_web_config.html`
- `notes.md`

## Stable Operating Workflow (Current)
- Set to Idle
- Full Read once (to refresh cached EEPROM-heavy fields)
- Fast Read for routine checks
- Before any write: Set to Idle -> Safe Write
- Use Power Cycle only for recovery when communication degrades

## Runtime Notes
- Fast Read is cache-first and avoids EEPROM-heavy reads to improve stability.
- Full Read may occasionally show EEPROM warnings, but fallback keeps the UI usable.
- Beacon should stay ON during normal node communication and configuration.

## расшифровка LED по TC‑Link‑200‑OEM, согласно руководству:

OFF — нода выключена.
Быстрое мигание зелёным при старте — загрузка/бут.
Медленный зелёный пульс (≈1 раз/сек) — Idle, ждёт команду.
Один зелёный всплеск раз в 2 сек — Sampling.
Синий во время sampling — resynchronizing (нода пытается восстановить синхронизацию).
Красный LED — ошибка самотеста (Built‑in test error).


### TEMP Note
Есть у нас (изменяемые):

    Sample Rate
    Transmit Power
    Input Range
    Active Channels (ch1/ch2)

Есть у нас, но read-only в статусе (не изменяемые в форме):
    Comm Protocol
    State
    Storage
    Sampling
    Data Mode
    Frequency/Region/Last Comm

Нет у нас (из списка SensorConnect):
    Transducer Type
    Sensor Type (RTD types)
    Wire Type
    Low Pass Filter / Filter Cutoff
    Linear Calibration Units (Ohm/mOhm/kOhm, C/F/K)
    Lost Beacon Timeout (toggle + value)
    Diagnostic Info Interval (toggle + value)
    Storage Limit Mode (Stop/Overwrite)
    Default Operation Mode (Idle/Sleep/Sample)
    User Inactivity Timeout
    Check Radio Interval


Что советую добавить следующим шагом (по полезности/риску):
    Low Pass Filter (3 значения)
    Storage Limit Mode (Stop/Overwrite)
    Lost Beacon Timeout + Diagnostic Info Interval
    Это даст максимум пользы при минимальном риске поломки чтения/записи.
