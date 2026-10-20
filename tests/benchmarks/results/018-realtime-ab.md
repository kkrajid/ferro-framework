# Feature 018 SC-005: HTTP performance before/after WebSockets and streaming

Measured 2026-10-12 on Windows 11 (8 logical CPUs, 5.9 GB RAM, other desktop applications
running), CPython 3.13, release builds (fat LTO) of two complete packages: **before** = commit
`03b44cb` (Feature 017), **after** = the Feature 018 working tree (before the stream-abort change,
which touches streamed bodies only). `ferro run --workers 4`, oha (100 connections, 10 s after a
3 s warm-up) on the same machine; the package was chosen per server start with `PYTHONPATH` and
the order alternated every round (method of [`ab_builds.py`](../ab_builds.py), which swaps only
the native module — not possible here because the Python side changed too). The Rust-only
baseline was not run (its build had been cleaned).

| Scenario | Before req/s (runs) | After req/s (runs) | Per-round after/before | Median |
|---|---|---|---|---|
| Hello world | 13,658; 15,747; 11,844; 14,515; 16,755 | 14,996; 12,222; 15,412; 13,804; 17,313 | 1.10, 0.78, 1.30, 0.95, 1.03 | **1.03×** |
| JSON route with a path parameter | 13,906; 10,894; 15,014; 16,069; 15,693 | 12,379; 11,423; 14,422; 16,260; 13,862 | 0.89, 1.05, 0.96, 1.01, 0.88 | **0.96×** |
| Database read by primary key (SQLite) | 6,435; 7,606; 7,898; 7,818; 7,950 | 7,110; 7,440; 7,844; 8,175; 6,277 | 1.10, 0.98, 0.99, 1.05, 0.79 | **0.99×** |

0 failed requests. All three medians are within the round-to-round spread of this machine
(0.78–1.30); no regression is measurable. The hot path change for plain HTTP is one `Option`
check for a streamed body and a boolean per route for WebSocket routes.
