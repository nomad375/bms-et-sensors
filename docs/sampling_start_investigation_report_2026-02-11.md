# Sampling Start Investigation Report (2026-02-11)

## Scope
Find why starting sampling via our configurator does not match SensorConnect behavior on the same node (`TC-Link-200-OEM`, node `16904`).

## Target Behavior
- SensorConnect start at `64 Hz` should be matched by our `/api/sampling/start/<node_id>` path.
- Streamed `ch1` data in Influx/Grafana should reflect actual configured sample rate.

## What Was Tested

### 1) Baseline with SensorConnect start (external app)
- Node started in SensorConnect at `64 Hz`, continuous.
- Measured in our streamer/Influx:
  - `hz_window = 63.4196`
  - `hz_span = 64.2523`
- Conclusion: hardware/base/stream pipeline can carry near-64Hz when start is done by SensorConnect.

### 2) Start via our API (before fixes)
- API payload: `sample_rate=112 (64 Hz), continuous=true, log_transmit_mode=transmit`.
- Measured in Influx: around `~1.8-2.0 Hz`.
- Logs showed start path often used `resendStartSyncSampling` (no `startSyncSampling` method exposed in this MSCL build).

### 3) Influx timestamp integrity fix
- Implemented explicit datapoint timestamp write (`dp.as_Timestamp()` -> `Point.time(..., NS)`), plus duplicate timestamp nano-offset.
- Result: data no longer overwritten in Influx.
- Verification at SensorConnect 8Hz:
  - 60s count: `480` points (`8.0 Hz`)
  - 30s count: `240` points (`8.0 Hz`)
- Conclusion: earlier undercount in Grafana/Influx was a real write-timestamp bug and is fixed.

### 4) Sampling start path hardening
- Removed permissive behavior that previously returned `start ok` after `applyConfig` failure.
- Now start returns error on config-apply failure.
- Removed forced data mode setter during start (`cfg.dataMode`/`cfg.dataCollectionMethod`) due compatibility issues (`Derived Data Mode is not supported by this Node`).

### 5) SyncSamplingNetwork migration attempts
Implemented and tested multiple variants:
- `SyncSamplingNetwork.addNode(node, cfg)` + `applyConfiguration` + `startSampling`
- `node.applyConfig(cfg)` first, then `SyncSamplingNetwork.addNode(node)` + `applyConfiguration` + `startSampling`
- strict fallback policy (no hidden `resend` on network start failure)

Observed errors during iterations:
- `Network configuration has not been applied. Cannot start sampling.`
- `Cannot apply network settings with a pending config.`

After aligning to manufacturer sequence (apply node config, then network add/apply/start):
- `start_method` moved to `sync-network`
- but measured data rate still remained ~`1.8-2.0 Hz` when started from our API.

## Current State (as of this report)
- **Resolved:** Influx point overwrite issue (timestamp precision bug).
- **Resolved:** false-positive "start ok" on failed config apply.
- **Resolved:** wrong sample-rate enum map in app code.
- **Status now:** our API start can reach real `64 Hz` after enum fix.

## Critical Root Cause Found (late update)
The app had an incorrect hardcoded `RATE_MAP`:
- app believed `112 == 64 Hz`
- actual MSCL build has `112 == 2 Hz` and `107 == 64 Hz`

Confirmed inside running container:
- `mscl.WirelessTypes.sampleRate_64Hz = 107`
- `mscl.WirelessTypes.sampleRate_2Hz = 112`

So when UI selected label `64 Hz`, backend often sent enum `112`, which starts at `2 Hz`.

## Verification After Fix
Changes:
- `app/mscl_constants.py`: build `RATE_MAP` from installed MSCL `WirelessTypes.sampleRate_*` constants (dynamic).
- `app/mscl_config.py`: `_sample_rate_label(...)` now prefers MSCL rate object text first.

Test:
- POST `/api/sampling/start/16904` with `sample_rate=107` (64 Hz), continuous.
- Logs:
  - `post-apply sampleRate ... 107 (64 Hz)`
  - `Packet rates (64Hz:...)`
- Influx (`ch1`) count over 30s: `1872` points (`62.4 Hz` average, near 64 Hz with packet/window jitter).

Conclusion:
- primary mismatch between SensorConnect and our configurator for this case was enum mapping, not transport.

## Key Evidence
- SensorConnect run: near-64Hz observed by our streamer.
- Our run: near-2Hz repeatedly (even with `sync-network(startSampling)` logged as sent).
- Node state remains `Sampling`, link `ok`.

## Most Likely Remaining Gap
A hidden difference in network-level sync setup between SensorConnect and our implementation (e.g. lossless/protocol/network refresh sequencing, pending-config lifecycle, or additional preconditions that SensorConnect applies before network start).

## Code Changes Made During Investigation
- `app/mscl_config.py`
  - Added datapoint timestamping in stream writes (`WritePrecision.NS`).
  - Added duplicate timestamp collision handling.
  - Hardened `/api/sampling/start` failure handling.
  - Removed forced data mode writes in start path.
  - Added active channel preservation in start config.
  - Added SyncSamplingNetwork-based start logic and retries/diagnostics.
- `app/mscl_constants.py`
  - Rate-map cleanup and model filtering updates during experiments.

## Recommended Next Steps
1. Capture and compare full node config snapshot before/after SensorConnect start (all readable fields), then before/after our start.
2. Explicitly set SyncSamplingNetwork options to match SensorConnect defaults:
   - `communicationProtocol(...)`
   - `lossless(...)`
   - `refresh()` before `startSampling()`.
3. Add post-start readback verification endpoint:
   - read effective sample rate from packet metadata (`sweep.sampleRate().prettyStr()`) in stream path.
4. If mismatch persists, isolate with minimal standalone script in container using the exact manufacturer sample flow against node `16904`, then port exact call order into Flask API.
