# MSCL Stream Shift and Resolution Test (2026-02-11)

- Node: `16904`
- Scenario: `Log and Transmit` for fixed duration, then wait for `Idle`, then export node datalog to Influx.
- Stream A: `mscl_config_stream` (radio).
- Stream B: `mscl_node_export` (node datalog export).

| Rate | Duration (s) | Radio count | Export count | Radio eff Hz | Export eff Hz | Export dt p50 (ms) | Nearest delta p50 (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 Hz | 60 | 56 | 58 | 1.0 | 1.0 | 1000.0 | -136.518166 |
| 8 Hz | 120 | 942 | 958 | 8.043 | 8.0 | 125.0 | 112.376462 |
| 64 Hz | 180 | 11438 | 11470 | 64.074 | 64.0 | 15.625 | 1.063342 |

## Notes

- `8 Hz` and `64 Hz` now preserve expected sub-second spacing in Node Export stream.
- Node Export time interpolation uses `tick + sample_rate` to avoid 1-second clustering.
- Remaining edge deltas (first/last) are startup/shutdown boundaries; median nearest delta stays close to 0..110 ms.

## Raw JSON

```json
{
  "node_id": 16904,
  "generated_utc": "2026-02-11T16:02:09Z",
  "tests": [
    {
      "rate_label": "1 Hz",
      "rate_enum": 113,
      "duration_sec": 60,
      "idle_confirmed": true,
      "radio_count": 56,
      "node_export_count": 58,
      "radio_effective_hz": 1.0,
      "node_export_effective_hz": 1.0,
      "radio_dt_stats_ms": {
        "min_ms": 0.051126,
        "p50_ms": 1000.012517,
        "p95_ms": 1000.59026,
        "max_ms": 2000.091875
      },
      "node_export_dt_stats_ms": {
        "min_ms": 1000.0,
        "p50_ms": 1000.0,
        "p95_ms": 1000.0,
        "max_ms": 1000.0
      },
      "export_written": 58,
      "export_skipped_existing": 0,
      "clock_offset_ns": 2867955818,
      "clock_skew_ns": 2867955818,
      "first_delta_ms_export_minus_radio": 1863.808109,
      "last_delta_ms_export_minus_radio": 3862.84922,
      "nearest_delta_export_to_radio_ms": {
        "min_ms": -156.430649,
        "p50_ms": -136.518166,
        "p95_ms": 863.863744,
        "max_ms": 3862.84922,
        "mean_ms": 52.771829
      }
    },
    {
      "rate_label": "8 Hz",
      "rate_enum": 110,
      "duration_sec": 120,
      "idle_confirmed": true,
      "radio_count": 942,
      "node_export_count": 958,
      "radio_effective_hz": 8.043,
      "node_export_effective_hz": 8.0,
      "radio_dt_stats_ms": {
        "min_ms": 0.019347,
        "p50_ms": 0.030312,
        "p95_ms": 999.817166,
        "max_ms": 2000.256915
      },
      "node_export_dt_stats_ms": {
        "min_ms": 125.0,
        "p50_ms": 125.0,
        "p95_ms": 125.0,
        "max_ms": 125.0
      },
      "export_written": 958,
      "export_skipped_existing": 0,
      "clock_offset_ns": 2867955818,
      "clock_skew_ns": 3168148048,
      "first_delta_ms_export_minus_radio": 1863.648422,
      "last_delta_ms_export_minus_radio": 4486.860306,
      "nearest_delta_export_to_radio_ms": {
        "min_ms": -887.804837,
        "p50_ms": 112.376462,
        "p95_ms": 488.334649,
        "max_ms": 4486.860306,
        "mean_ms": 134.106765
      }
    },
    {
      "rate_label": "64 Hz",
      "rate_enum": 107,
      "duration_sec": 180,
      "idle_confirmed": true,
      "radio_count": 11438,
      "node_export_count": 11470,
      "radio_effective_hz": 64.074,
      "node_export_effective_hz": 64.0,
      "radio_dt_stats_ms": {
        "min_ms": 0.017042,
        "p50_ms": 0.026889,
        "p95_ms": 249.433157,
        "max_ms": 506.368735
      },
      "node_export_dt_stats_ms": {
        "min_ms": 15.625,
        "p50_ms": 15.625,
        "p95_ms": 15.625,
        "max_ms": 15.625
      },
      "export_written": 11470,
      "export_skipped_existing": 0,
      "clock_offset_ns": 2867955818,
      "clock_skew_ns": 5303027360,
      "first_delta_ms_export_minus_radio": 3356.565778,
      "last_delta_ms_export_minus_radio": 4062.904655,
      "nearest_delta_export_to_radio_ms": {
        "min_ms": -249.203778,
        "p50_ms": 1.063342,
        "p95_ms": 111.097616,
        "max_ms": 4062.904655,
        "mean_ms": 40.333937
      }
    }
  ]
}
```