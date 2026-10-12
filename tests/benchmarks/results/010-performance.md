# Feature 010 results: performance suite & multi-process serving

Measured 2026-10-11 06:18 UTC on Windows-11-10.0.26200-SP0, 8 logical CPUs, CPython 3.13.12, release build, oha (100 connections, 10 s after a 3 s warm-up) on the same machine, 3 rounds with the scenarios alternating; medians.

1-process scenarios use `--workers 4`; 4-process scenarios use `--workers 2` per process.

| Scenario | req/s (median) | runs | p50 ms | p99 ms | failed | vs Rust baseline |
|---|---|---|---|---|---|---|
| Rust-only baseline (engine, no Python) | 13,429 | 13,429, 14,068, 12,992 | 5.46 | 50.57 | 0 | 100% |
| hello world, 1 process | 9,851 | 8,549, 9,851, 11,293 | 6.40 | 68.45 | 0 | 73% |
| hello world, 4 processes | 5,752 | 5,752, 5,465, 6,352 | 11.24 | 131.98 | 0 | 43% |
| JSON route with a path parameter | 10,626 | 9,532, 10,626, 12,011 | 6.17 | 79.44 | 0 | 79% |
| database read by primary key (SQLite) | 5,761 | 6,156, 5,761, 4,334 | 13.93 | 104.78 | 0 | 43% |
| database read, 4 processes | 4,849 | 5,209, 4,833, 4,849 | 13.69 | 110.47 | 0 | 36% |
| CPU-heavy sync route, 1 process | 227 | 202, 251, 227 | 411.00 | 998.27 | 0 | 2% |
| CPU-heavy sync route, 4 processes | 694 | 679, 705, 694 | 121.31 | 518.81 | 0 | 5% |
| hello world + 1 pass-through middleware | 10,878 | 10,878, 9,789, 11,690 | 6.16 | 82.78 | 0 | 81% |
| authenticated request (session lookup) | 2,321 | 2,144, 2,321, 2,618 | 40.09 | 100.82 | 0 | 17% |

| Criterion | Target | Result | Verdict |
|---|---|---|---|
| SC-001 | CPU-heavy route, 4 processes >= 2.5x of 1 | 3.06x | PASS |
| SC-002 | hello world, 4 processes >= 1.3x of 1 | 0.58x | FAIL |
| SC-003 | 0 failed requests in the suite | 0 | PASS |
| SC-003 | a worker killed during a 10 s load run (4 processes, 100 clients): < 1% failed | 23 of 69,406 (0.033%) | PASS |
| - | database read, 4 processes vs 1 | 0.84x | reported |

Headroom: hello world in one process reached 73% of the Rust-only baseline, so SC-002 asks 4 processes for 95% of what the engine alone serves with the client on the same machine. With 4 processes instead of one: CPU-heavy route 3.06x, database read 0.84x, hello world 0.58x.
