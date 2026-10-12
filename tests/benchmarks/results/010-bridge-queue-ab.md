# Feature 010 US3: bridge job-queue change, before/after

Measured 2026-10-11 on Windows 11 (8 logical CPUs, 5.9 GB RAM, other desktop applications
running), CPython 3.13, two release builds of the native module, `ferro run --workers 4`, oha
(100 connections, 10 s after a 3 s warm-up) on the same machine. Method:
[`ab_builds.py`](../ab_builds.py) — the two builds are swapped before every server start and
their order alternates each round, so slow phases of the machine hit both alike.

**Profile that justified the change** (`run_010.py --profile`, cProfile of the Python thread):

- Database read route: `call_soon_threadsafe` ran once per query result (28,047 calls in 10 s),
  each from a database thread that had to take the interpreter lock and write asyncio's
  self-pipe; `_write_to_self` (3.3 s cumulative) and the loop's `_loop_self_reading` (3.8 s)
  together exceeded half of the 10 s window.
- Hello world: the loop woke about once per 19 requests; the self-pipe wake-ups
  (`_loop_self_reading` 2.0 s + `_write_to_self` 0.9 s of about 13 s) were above the 5% bar.

**Change**: database completions join the bridge's batched job queue (`Job::Call`) instead of
calling `call_soon_threadsafe` themselves, and a drain keeps its "pending" flag while it works,
re-checking the queue (up to 4 passes) before clearing it, so work queued during a drain never
wakes the loop.

| Scenario | Before req/s (median; runs) | After req/s (median; runs) | Per-round after/before | Median |
|---|---|---|---|---|
| Database read by primary key (SQLite) | 2,590 (2,590; 3,540; 2,343; 2,090; 2,667) | 7,937 (8,322; 8,473; 7,937; 7,029; 6,328) | 3.21, 2.39, 3.39, 3.36, 2.37 | **3.21×** |
| Hello world | 14,606 (13,032; 10,533; 14,606; 17,432; 17,593) | 17,258 (19,919; 15,457; 17,258; 17,141; 19,157) | 1.53, 1.47, 1.18, 0.98, 1.09 | **1.18×** |
| JSON route with a path parameter | 14,204 (15,204; 14,255; 14,204; 12,587; 12,151) | 15,452 (16,424; 13,000; 15,452; 21,218; 12,833) | 1.08, 0.91, 1.09, 1.69, 1.06 | **1.08×** |

The Rust-only baseline measured in the same rounds ranged from 13,791 to 21,810 req/s, which
shows how much this machine varied during the session; the per-round ratios are the reliable
figures. 0 failed requests in all runs; all Python and Rust tests pass with the change.
