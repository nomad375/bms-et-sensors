# Slow Sampling Rates Verification (5 min each)

Node: 16904
UTC start: 2026-02-11T10:59:35Z
Method: each mode started via /api/sampling/start for 300s, then stopped.

| Target | Enum | Points/300s | Measured Hz | Mean interval (s) | Note |
|---|---:|---:|---:|---:|---|
| every 2 seconds | 114 | 149 | 0.496667 | 1.547 |  |
| every 30 seconds | 117 | 10 | 0.033333 | 29.889 |  |
| every 2 minutes | 119 | 3 | 0.010000 | 119.500 | low sample count expected in 5 min window |

UTC end: 2026-02-11T11:14:58Z
