# UI Dropdown Map: Readable Fields vs MSCL Fields

This file maps dropdown menus in the configurator UI to:
- what the user sees (readable field/label),
- what backend JSON fields are used (`/api/read`, `/api/write`, `/api/sampling/start`),
- what MSCL methods are used behind the scenes.

## Sampling Panel Dropdowns

### 1) Sample Rate
- `UI`: `Sample Rate`
- `id`: `samplingRunRate`
- `Read source`: built from `supported_rates` + `current_rate` (copied from main `sampleRate`)
- `Write key`: `sample_rate` (`/api/sampling/start`)
- `MSCL`: `cfg.sampleRate(...)` in `_start_sampling_run`

### 2) Log/Transmit
- `UI`: `Log/Transmit`
- `id`: `samplingRunMode`
- `Read source`: static UI options (`log`, `transmit`, `log_and_transmit`)
- `Write key`: `log_transmit_mode`
- `MSCL`: `_set_sampling_mode_on_node` -> `cfg.dataMode(...)` or `cfg.dataCollectionMethod(...)`

### 3) Data Type
- `UI`: `Data Type`
- `id`: `samplingRunDataType`
- `Read source`: static option (`float`)
- `Write key`: `data_type`
- `MSCL`: no direct write (used as run metadata)

### 4) Mode (`for` / `continuously`)
- `UI`: `Mode`
- `id`: `samplingRunWhen`
- `Read source`: static UI options
- `Write key`: `continuous`
- `MSCL`: no direct write (controls app-side timer behavior)

### 5) Duration Units
- `UI`: `Units` (`seconds` / `minutes` / `hours`)
- `id`: `samplingRunDurationUnits`
- `Read source`: static UI options
- `Write key`: `duration_units` (+ `duration_value`)
- `MSCL`: no direct write (converted to `duration_sec`, used by `_schedule_idle_after`)

## Node Settings Dropdowns

### 1) Transducer Type
- `UI`: `Transducer Type`
- `id`: `transducerType`
- `Read source`: `transducer_options`, `current_transducer_type`
- `Write key`: `transducer_type`
- `MSCL read`: `getTempSensorOptions().transducerType()`, `features.transducerTypes()`
- `MSCL write`: TempSensorOptions flow via `_set_temp_sensor_options`

### 2) Sensor Type
- `UI`: `Sensor Type`
- `id`: `sensorType`
- `Read source`: `rtd_sensor_options` / `thermistor_sensor_options` / `thermocouple_sensor_options`, `current_sensor_type`
- `Write key`: `sensor_type`
- `MSCL read`: `getTempSensorOptions()` -> `rtdType/thermistorType/thermocoupleType`
- `MSCL write`: TempSensorOptions constructors

### 3) Wire Type
- `UI`: `Wire Type`
- `id`: `wireType`
- `Read source`: `rtd_wire_options`, `current_wire_type`
- `Write key`: `wire_type`
- `MSCL read`: `getTempSensorOptions().rtdWireType()`
- `MSCL write`: TempSensorOptions RTD wire

### 4) Input Range
- `UI`: `Input Range`
- `id`: `inputRange`
- `Read source`: `supported_input_ranges`, `current_input_range`
- `Write key`: `input_range`
- `MSCL read`: `node.getInputRange(ch1_mask())`, `features.inputRanges()`
- `MSCL write`: `cfg.inputRange(...)`

### 5) Low Pass Filter
- `UI`: `Low Pass Filter`
- `id`: `lowPassFilter`
- `Read source`: `low_pass_options`, `current_low_pass`
- `Write key`: `low_pass_filter`
- `MSCL read`: `node.getLowPassFilter(ch1_mask())`, `features.lowPassFilters()`
- `MSCL write`: `cfg.lowPassFilter(...)`

### 6) Calibration Unit
- `UI`: `Calibration Unit`
- `id`: `calibrationUnit`
- `Read source`: `unit_options`, `current_unit`
- `Write key`: `unit`
- `MSCL read`: `node.getUnit(ch1_mask())` or `node.getUnit()`, `features.units(...)`
- `MSCL write`: `cfg.unit(ch1_mask(), ...)` or `cfg.unit(...)`

### 7) CJC Unit
- `UI`: `CJC Unit`
- `id`: `cjcCalibrationUnit`
- `Read source`: `cjc_unit_options`, `current_cjc_unit`
- `Write key`: `cjc_unit`
- `MSCL read`: `node.getUnit(ch2_mask())`, `features.units(ch2_mask())`
- `MSCL write`: `cfg.unit(ch2_mask(), ...)`

### 8) Sample Rate
- `UI`: `Sample Rate`
- `id`: `sampleRate`
- `Read source`: `supported_rates`, `current_rate`
- `Write key`: `sample_rate` (`/api/write`)
- `MSCL read`: `node.getSampleRate()`, `features.sampleRates(...)`
- `MSCL write`: `cfg.sampleRate(...)`

### 9) Data Mode
- `UI`: `Data Mode`
- `id`: `dataMode`
- `Read source`: `data_mode_options`, `current_data_mode`
- `Write key`: `data_mode`
- `MSCL read`: `node.getDataMode()`, `features.dataModes()`
- `MSCL write`: `cfg.dataMode(...)` or `cfg.dataCollectionMethod(...)`

### 10) Storage Limit Mode
- `UI`: `Storage Limit Mode`
- `id`: `storageLimitMode`
- `Read source`: `storage_limit_options`, `current_storage_limit_mode`
- `Write key`: `storage_limit_mode`
- `MSCL read`: `node.getStorageLimitMode()`, `features.storageLimitModes()`
- `MSCL write`: `cfg.storageLimitMode(...)`

### 11) TX Power
- `UI`: `TX Power`
- `id`: `txPower`
- `Read source`: `current_power` (dBm)
- `Write key`: `tx_power`
- `MSCL read`: `node.getTransmitPower()` (enum -> dBm map)
- `MSCL write`: `cfg.transmitPower(tx_enum)`

### 12) Default Mode
- `UI`: `Default Mode`
- `id`: `defaultMode`
- `Read source`: `default_mode_options`, `current_default_mode`
- `Write key`: `default_mode`
- `MSCL read`: `node.getDefaultMode()`, `features.defaultModes()`
- `MSCL write`: `cfg.defaultMode(...)`

## Visibility / Capability Notes

- Some dropdowns are hidden/disabled if feature support is missing:
  - `supports_default_mode`
  - `supports_inactivity_timeout`
  - `supports_check_radio_interval`
  - `supports_transducer_type`
  - `supports_temp_sensor_options`
- UI behavior is capability-driven from `/api/read/<node_id>` payload.

## Relevant Files

- UI template and JS: `app/templates/mscl_web_config.html`
- Backend read/write/start handlers: `app/mscl_config.py`

## SensorConnect Alignment Plan (No Code Changes Yet)

### Goal

Align configurator dropdown fields with SensorConnect behavior and naming, while preserving MSCL-compatible writes.  
This phase is documentation and analysis only.

### Working Rules

- No edits to Python/HTML/JS code in this phase.
- Every SensorConnect screenshot is logged before any implementation decision.
- Each mismatch is classified as one of:
  - label/UX mismatch,
  - option-list mismatch,
  - default-value mismatch,
  - read mapping mismatch,
  - write mapping mismatch,
  - unsupported-by-MSCL (cannot be mirrored exactly).

### Screenshot Intake Template

For each screenshot you send, record:

- `Source screen`: (e.g., Node settings, Sampling settings, Advanced)
- `Field label in SensorConnect`:
- `Options visible`:
- `Selected/default value`:
- `Our UI field match`: (id + label from this file)
- `MSCL read field/method`:
- `MSCL write field/method`:
- `Gap type`: (from the list above)
- `Proposed action`: (rename, reorder options, add converter, hide field, etc.)
- `Risk`: low/medium/high

### Comparison Workflow

1. Capture SensorConnect field exactly as shown (label, options, selected value).
2. Match it to existing UI id and backend write key in this map.
3. Verify whether MSCL exposes both read and write for that parameter.
4. Mark gap and propose minimal change.
5. Queue item for implementation batch only after confirmation.

### Decision Policy

- Prefer SensorConnect label wording in UI if MSCL semantic meaning is the same.
- Keep internal write keys stable unless backend/API compatibility requires change.
- If SensorConnect combines fields but MSCL requires separate values, keep split fields and document why.
- If MSCL does not support a SensorConnect-visible parameter, mark as `Not supported by MSCL` and do not emulate blindly.

### Validation Criteria Before Coding

- Every dropdown in our UI has:
  - confirmed SensorConnect counterpart or explicit `no counterpart`,
  - confirmed MSCL read/write path,
  - approved action decision.
- High-risk gaps (mode changes, sampling lifecycle, default mode behavior) have rollback notes.

### Rollback Planning (for future implementation stage)

- Keep pre-change snapshot of:
  - `app/templates/mscl_web_config.html`
  - `app/mscl_config.py`
- Apply changes in small batches (labels first, then option mapping, then behavior).
- After each batch:
  - run sampling start/idle sanity checks,
  - verify `/api/read/<node_id>` and `/api/write/<node_id>` payload compatibility.
- If regression appears, revert last batch only and log root cause.

### Tracking Checklist

- [x] Receive first SensorConnect screenshot set.
- [ ] Map Sampling panel fields.
- [ ] Map Node settings fields.
- [ ] Map advanced/system fields (if present).
- [ ] Tag all mismatches by type.
- [ ] Approve implementation backlog (no code yet).

## Screenshot Intake Log

### Batch 1: General Screens (Received)

- `Status`: received and logged
- `Scope`: high-level layout + tab structure + currently selected values (without fully expanded dropdown option lists)
- `Node in screenshots`: `Node 16904` (TC-Link-200-OEM)

#### 1) Options Page (Control / Setup / Advanced tiles)

- `Source screen`: Node options landing page
- `Observed controls`: `Sample`, `Set to Idle`, `Sleep`, `Configure`, `Cycle Power`, `Range Test`, `Download Datalog Sessions`, `Communication Protocol`, `Export Configuration`, `Import Configuration`, `Upgrade Firmware`, `Read/Write EEPROM`
- `Our UI field match`: partial overlap only (our configurator focuses on config + sampling control, not all service operations)
- `Gap type`: label/UX mismatch (navigation/feature surface differs)
- `Proposed action`: no immediate change; evaluate only fields that map to our configurator scope
- `Risk`: low

#### 2) Wireless Node Configuration -> Hardware tab

- `Observed fields`:
  - `Transducer Type` -> selected `RTD`
  - `Sensor Type` -> selected `Uncompensated (...)`
  - `Wire Type` -> selected `2 Wire`
  - `Low Pass Filter` -> selected `294 Hz`
  - `Input Range` -> selected `+/-13.5 V or 0 to 1 mega-ohms`
- `Our UI field match`:
  - `transducerType`, `sensorType`, `wireType`, `lowPassFilter`, `inputRange`
- `Gap type`: pending exact option-list comparison (need expanded dropdown screenshots)
- `Proposed action`: compare order, naming, units, and hidden/conditional logic after dropdown captures
- `Risk`: medium

#### 3) Wireless Node Configuration -> Calibration tab

- `Observed fields`:
  - Channel `Temperature (ch1)` unit -> selected `Ohm`
  - Channel `CJC Temperature (ch2)` unit -> selected `Celsius`
- `Our UI field match`:
  - `calibrationUnit` (ch1), `cjcCalibrationUnit` (ch2)
- `Gap type`: pending option-list comparison
- `Proposed action`: verify unit label parity (`Celsius`, `Ohm`, etc.) and per-channel behavior
- `Risk`: low

#### 4) Wireless Node Configuration -> Sampling tab

- `Observed fields`:
  - `Lost Beacon Timeout` (toggle + value + units)
  - `Diagnostic Info Interval` (toggle + value + units)
  - `Storage Limit Mode` -> selected `Overwrite`
- `Our UI field match`:
  - `Storage Limit Mode` -> `storageLimitMode`
  - Timeout/interval controls are currently outside our dropdown map scope
- `Gap type`: scope mismatch + pending field support mapping
- `Proposed action`: include timeout/interval controls in advanced mapping pass (not dropdown-only pass)
- `Risk`: medium

#### 5) Wireless Node Configuration -> Power tab

- `Observed fields`:
  - `Default Operation Mode` -> selected `Sample`
  - `User Inactivity Timeout` (toggle + value + units)
  - `Check Radio Interval` value + units
  - `Transmit Power` -> selected `10 dBm`
- `Our UI field match`:
  - `defaultMode`, `txPower`
  - `supports_inactivity_timeout` / `supports_check_radio_interval` gated fields
- `Gap type`: pending option-list comparison + possible label mismatch (`Default Operation Mode` vs `Default Mode`)
- `Proposed action`: confirm exact dropdown options and unit rendering before backlog definition
- `Risk`: medium

### Next Screenshot Requests (Dropdown Expansion Needed)

- Expand and screenshot option lists for:
  - `Transducer Type`
  - `Sensor Type`
  - `Wire Type`
  - `Low Pass Filter`
  - `Input Range`
  - Calibration units for both channels
  - `Storage Limit Mode`
  - `Default Operation Mode`
  - `Transmit Power`
- If available, include currently selected value in each expanded list capture.

### Batch 2: Hardware Dropdowns (Expanded, Compared)

- `Status`: compared against current code paths (`app/mscl_constants.py`, `app/mscl_config.py`, `app/templates/mscl_web_config.html`)
- `Scope`: `Hardware` tab dropdown content only

#### A) `Transducer Type`

- `SensorConnect options seen`: `RTD`, `Thermocouple`, `Thermistor`
- `Our options source`: `TRANSDUCER_LABELS`
- `Current parity`: full content match
- `Gap type`: none (content), possible order drift by feature-return order
- `Proposed action`: keep values; optionally normalize display order to SensorConnect (`RTD`, `Thermocouple`, `Thermistor`) if node/feature order differs
- `Risk`: low

#### B) `Sensor Type` (RTD mode)

- `SensorConnect options seen`: `Uncompensated (Resistance)`, `PT10`, `PT50`, `PT100`, `PT200`, `PT500`, `PT1000`
- `SensorConnect extra text`: secondary line under each PT option (`-200 to 850 °C`)
- `Our options source`: `RTD_SENSOR_LABELS`
- `Current parity`: primary option names match
- `Gap type`: label/UX mismatch (we do not render secondary temperature range line)
- `Proposed action`: keep enum mapping as-is; optional UI enhancement to show helper range text for RTD presets
- `Risk`: low

#### C) `Wire Type`

- `SensorConnect options seen`: `2 Wire`, `3 Wire`, `4 Wire`
- `Our options source`: `RTD_WIRE_LABELS`
- `Current parity`: full match
- `Gap type`: none
- `Proposed action`: no change needed
- `Risk`: low

#### D) `Low Pass Filter` (`Filter Cutoff`)

- `SensorConnect options seen`: `294 Hz`, `12.66 Hz (92db 50/60 Hz rejection)`, `2.6 Hz (120db 50/60 Hz rejection)`
- `Our options source`: `LOW_PASS_LABELS`
- `Current parity`: full match by content
- `Gap type`: minor label formatting only (`db` casing already same in our constants)
- `Proposed action`: no functional change
- `Risk`: low

#### E) `Input Range`

- `SensorConnect options seen`:
  - `+/-1.35 V or 0 to 1 mega-ohms` (`Gain: 1`)
  - `+/-1.25 V or 0 to 10000 ohms` (`Gain: 2`)
  - `+/-625 mV or 0 to 3333.3 ohms` (`Gain: 4`)
  - `+/-312.5 mV or 0 to 1428.6 ohms` (`Gain: 8`)
  - `+/-156.25 mV or 0 to 666.67 ohms` (`Gain: 16`)
  - `+/-78.125 mV or 0 to 322.58 ohms` (`Gain: 32`)
  - `+/-39.0625 mV or 0 to 158.73 ohms` (`Gain: 64`)
  - `+/-19.5313 mV or 0 to 78.74 ohms` (`Gain: 128`)
- `Our options source`: `supported_input_ranges` + `INPUT_RANGE_LABELS`
- `Current parity`: partial
- `Gap type`:
  - option-list mismatch risk for higher gains (`32/64/128`) because current static label map documents only up to `Gain: 16`;
  - label/UX mismatch: SensorConnect shows `Gain` on separate line, we render one-line labels.
- `Proposed action`:
  - extend `INPUT_RANGE_LABELS` coverage for all ranges returned by `features.inputRanges()` on this node;
  - keep write values unchanged (enum IDs), update display labels only;
  - optional UI formatting for two-line display (`range` + `Gain: X`) if readability is required.
- `Risk`: medium

### Hardware Comparison Outcome

- `Ready for implementation backlog`: yes (hardware dropdowns now classified)
- `No-code decision`: enum/write mapping can stay stable; needed changes are mostly label/option completeness and UI readability.

### Batch 3: Sampling Menu (Expanded, Partial)

- `Status`: compared for currently provided controls
- `Scope`: `Sampling` tab snippet (`Lost Beacon Timeout`, `Diagnostic Info Interval`, `Storage Limit Mode`)

#### A) `Storage Limit Mode`

- `SensorConnect options seen`: `Stop`, `Overwrite`
- `Selected in screenshot`: `Overwrite`
- `Our UI field match`: `storageLimitMode` (`Storage Limit Mode`)
- `Our options source`: `storage_limit_options` from `/api/read` (`features.storageLimitModes()` fallback to `Overwrite/Stop`)
- `Current parity`: full content match (same two options)
- `Gap type`: none (content); possible order-only difference (`Stop/Overwrite` vs `Overwrite/Stop`)
- `Proposed action`: keep enum/write mapping unchanged; optionally normalize option order to SensorConnect
- `Risk`: low

#### B) `Lost Beacon Timeout`

- `SensorConnect control type`: toggle + numeric + units (`minute(s)`) (not dropdown)
- `Our UI mapping`: currently outside dropdown parity scope
- `Gap type`: scope mismatch (control type mismatch)
- `Proposed action`: track in advanced controls alignment batch, not dropdown batch
- `Risk`: medium

#### C) `Diagnostic Info Interval`

- `SensorConnect control type`: toggle + numeric + units (`second(s)`) (not dropdown)
- `Our UI mapping`: currently outside dropdown parity scope
- `Gap type`: scope mismatch (control type mismatch)
- `Proposed action`: track in advanced controls alignment batch, not dropdown batch
- `Risk`: medium

### Next Screenshot Requests (Remaining Dropdowns)

- Expand and screenshot option lists for:
  - `Default Operation Mode` (Power tab)
  - `Transmit Power` (Power tab)
  - Calibration units for both channels (if any additional unit options beyond current selection)

### Batch 4: Power Dropdowns (Expanded, Compared)

- `Status`: compared for provided dropdown captures
- `Scope`: `Power` tab dropdowns only

#### A) `Default Operation Mode`

- `SensorConnect options seen`: `Idle`, `Sleep`, `Sample`
- `Selected in screenshot`: `Sample`
- `Our UI field match`: `defaultMode` (`Default Operation Mode`)
- `Our options source`: `default_mode_options` from `/api/read` (`features.defaultModes()` + fallback + `_filter_default_modes`)
- `Current parity`: partial
- `Gap type`:
  - label/UX mismatch: our fallback label is `Sample (Sync)` while SensorConnect shows `Sample`;
  - option-list mismatch risk: our constants include `Low Duty Cycle` (`value=1`) but SensorConnect list for this node does not show it.
- `Proposed action`:
  - keep enum values unchanged;
  - display label `Sample` (or `Sample (Sync)` as secondary note only);
  - ensure final list is node-capability-driven and does not force `Low Duty Cycle` when SensorConnect/MSCL feature list omits it.
- `Risk`: medium

#### B) `Transmit Power`

- `SensorConnect options seen`: `10 dBm`, `5 dBm`, `0 dBm`
- `Selected in screenshot`: `10 dBm`
- `Our UI field match`: `txPower` (currently labeled `Radio Power`)
- `Our options source`: static HTML options in `app/templates/mscl_web_config.html`
- `Current parity`: partial
- `Gap type`:
  - option-list mismatch: our static list currently includes `16 dBm` and omits SensorConnect-style restricted list for this node;
  - label/UX mismatch: `Radio Power` vs `Transmit Power`, and `0 dBm (Min)` vs `0 dBm`.
- `Proposed action`:
  - move TX power options to capability/read-driven rendering (not hardcoded list), bounded by what node/MSCL exposes;
  - align label to `Transmit Power`;
  - keep write mapping `tx_power` unchanged.
- `Risk`: medium

### Power Comparison Outcome

- `Ready for implementation backlog`: yes
- `No-code decision`: backend write keys stay stable; main work is option source normalization and UI label alignment.

## Implementation Status (Applied in Code)

- `Scope`: align configurator values to `TC-Link-200-OEM` SensorConnect-visible options.

### Done

- Added missing `Input Range` labels for higher gains:
  - `Gain: 32`, `Gain: 64`, `Gain: 128`
- `Default Operation Mode` alignment:
  - display label normalized to `Sample` (instead of `Sample (Sync)`)
  - for `TC-Link-200-OEM`, default mode options filtered to `Idle`, `Sleep`, `Sample`
- `Transmit Power` alignment:
  - UI label renamed to `Transmit Power`
  - options now backend-driven (`tx_power_options`) instead of hardcoded HTML list
  - for `TC-Link-200-OEM`, options constrained to `10 dBm`, `5 dBm`, `0 dBm`
  - write-path clamps unsupported TX values to model-allowed values
- `Sampling Start` alignment:
  - `Data Type` pinned to `float (raw data)` in UI and server path (`data_type` forced to `float`)
  - `Mode` default set to `continuously`; only `continuously` + `for` remain
  - duration unit labels aligned to SensorConnect-style capitalization (`Seconds/Minutes/Hours`)
  - channel label aligned to SensorConnect (`Temperature (ch1)`)
  - sample rate labels now use MSCL string values when available (to expose model-supported low-frequency interval labels like `every N seconds/minutes`)

### Notes

- Write keys/enums are unchanged; only visibility/labeling and model-specific filtering were adjusted.

## Next Phase: Sampling Start Menu (Ready for Screenshot Intake)

### What We Will Compare

- Start action semantics:
  - `Sample` button behavior in SensorConnect
  - our sampling start flow (`/api/sampling/start/<node_id>`)
- Pre-start required state:
  - must be `Idle` before start or not
  - what auto-transitions are applied by each app
- Sampling start parameter set:
  - sample rate
  - log/transmit mode (or data mode equivalent)
  - duration mode (`for` / `continuous`)
  - duration value + units
  - data type (if shown)

### Screenshot Checklist (Send in This Order)

1. Sampling start dialog/panel before pressing start (full view).
2. Every dropdown in this panel in expanded state (options visible).
3. Selected values just before pressing start.
4. Confirmation/result message after pressing start.
5. Node state indicator at +10s, +60s, +180s after start.

### Intake Template for Each Sampling Field

- `SensorConnect field label`:
- `Visible options`:
- `Selected value`:
- `Our UI field/id`:
- `Our write key`:
- `MSCL method/behavior`:
- `Gap type`:
- `Proposed action`:
- `Risk`:

### Locked Decisions (From Product Direction)

- `On events` mode: **do not implement** in our configurator.
- `Data Type`: keep only **`float (raw data)`** for now.
- Any SensorConnect options outside these decisions are documented but excluded from implementation scope.

### Batch 5: Sampling Start Menu (Expanded, Compared)

- `Status`: compared from provided screenshots
- `Scope`: SensorConnect `Wireless Network` sampling row configuration

#### A) `Channels`

- `SensorConnect options seen`: `Temperature (ch1)`, `CJC Temperature (ch2)` (checkbox list)
- `Current selection in screenshot`: only `Temperature (ch1)` active
- `Our UI mapping`: channels list sent in write payload (`channels`)
- `Gap type`: UX mismatch (SensorConnect uses inline multiselect dropdown table cell)
- `Proposed action`: keep our channel selection model; ensure both channels remain available where supported
- `Risk`: low

#### B) `Sampling` rate dropdown

- `SensorConnect options seen` (examples from screenshots):
  - Hz group: `128, 64, 32, 16, 8, 4, 2, 1 Hz`
  - interval group: `every 2 seconds`, `every 5 seconds`, `every 10 seconds`, `every 30 seconds`, `every 1/2/5/10/30/60 minutes`
- `Current selection in screenshot`: `4 Hz continuously`
- `Our UI mapping`: `sampleRate` / `samplingRunRate` from `supported_rates`
- `Gap type`: option-list mismatch (we currently focus on rate enums surfaced by MSCL for this model; interval-form labels may be missing in UI)
- `Proposed action`: map all MSCL-supported low-frequency enums to SensorConnect-style labels (`every N seconds/minutes`) where available; do not fabricate unsupported values
- `Risk`: medium

#### C) `Run mode` inside Sampling cell

- `SensorConnect options seen`: `continuously`, `for`, `on events`
- `Locked scope`: `on events` excluded
- `Our UI mapping`: `samplingRunWhen` (`continuous` vs duration-based run)
- `Gap type`: intentional scope reduction
- `Proposed action`: keep only `continuously` + `for`; explicitly hide/disable `on events` equivalents
- `Risk`: low

#### D) `Duration` controls

- `SensorConnect controls seen`: numeric value + units dropdown (`Seconds`, etc.) used with `for`
- `Our UI mapping`: `duration_value` + `duration_units`
- `Gap type`: none (functional parity present)
- `Proposed action`: keep current implementation; ensure labels/units match SensorConnect naming
- `Risk`: low

#### E) `Data Type`

- `SensorConnect options seen`: `Calibrated`, `float`
- `Locked scope`: keep only `float (raw data)`
- `Our UI mapping`: `samplingRunDataType` currently static `float`
- `Gap type`: intentional scope reduction
- `Proposed action`: keep static single option (`float`) and do not add `Calibrated` yet
- `Risk`: low

#### F) `Log/Transmit`

- `SensorConnect options seen`: `Log`, `Transmit`, `Log and Transmit`
- `Our UI mapping`: `samplingRunMode` (`log`, `transmit`, `log_and_transmit`)
- `Gap type`: none (content parity)
- `Proposed action`: keep as-is; ensure label text exactly `Log and Transmit`
- `Risk`: low
