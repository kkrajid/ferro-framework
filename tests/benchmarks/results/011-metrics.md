# Feature 011 results: metrics overhead

Measured 2026-10-11 13:59 UTC on Windows-11-10.0.26200-SP0, 8 logical CPUs, CPython 3.13.12, release build, `ferro run --workers 4`, hello world, 100,000 requests per run (oha, 100 connections) after a 2 s warm-up, 6 rounds alternating metrics off/on.

| Metrics | server CPU per request (median) | runs (us) | req/s (median) | runs |
|---|---|---|---|---|
| off | 120.9 us | 119.7, 121.1, 133.8, 120.6, 137.5, 115.9 | 25,418 | 25,071, 26,869, 20,429, 25,765, 19,158, 30,467 |
| on | 121.9 us | 110.6, 119.2, 106.7, 130.2, 133.8, 124.5 | 23,898 | 32,280, 23,072, 32,930, 20,888, 22,762, 24,723 |

Per-round CPU ratios on/off: 0.924, 0.985, 0.798, 1.079, 0.973, 1.074; median cost -2.1% (SC-005 target: at most +5%) - PASS.
