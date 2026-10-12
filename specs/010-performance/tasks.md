---
description: "Task list for Feature 010 — Performance Suite & Multi-Process Serving"
---

# Tasks: Performance Suite & Multi-Process Serving

**Tests**: Required; each phase's tests are written and observed failing first.

## Phase 1: US1 - Multi-process serving (P1) 🎯

- [X] T001 [US1] Tests `tests/integration/test_processes.py`: workers share the port (≥ 2 pids),
  a killed worker is replaced (≤ 1 failed batch), Ctrl+Break stops all workers with exit 0 and
  "3 processes" in the banner, port in use → one error + exit 3, `ferro dev --processes 2` →
  exit 2, workers failing at startup → exit 1 "failed to start"; settings validation in
  `tests/python/test_config.py`
- [X] T002 [US1] Native: `ferro_http::start_on(listener)`; bridge `Server.start(listener)` with
  `adopt_listener` (the bridge's `unsafe`, with `// SAFETY:`)
- [X] T003 [US1] `processes` setting; `ServerRunner`/`serve(listener=...)`;
  `ferro/supervisor.py`; `--processes` and the hidden `_worker` command; `App.run()` refusal
- [X] T004 [US1] Tests: every worker uses the same database (acceptance 5); workers exit when the
  supervisor is killed (edge case)

## Phase 2: US2 - Performance suite (P1)

- [X] T005 [US2] `tests/benchmarks/run_010.py`: Rust baseline, hello 1/4 processes, JSON route,
  DB read 1/4 processes, CPU route 1/4 processes, middleware, authenticated request, a worker
  killed during load; medians of alternating rounds; results file + raw samples

## Phase 3: US3 - Measured optimizations (P3)

- [X] T006 [US3] `run_010.py --profile`: cProfile of the Python thread under load (hello, DB read)
- [X] T007 [US3] Bridge: database completions join the batched job queue; the drain keeps its
  flag while working and re-checks the queue (justified by the profile; before/after measured
  with `tests/benchmarks/ab_builds.py`)

## Phase 4: Polish

- [X] T008 [P] Docs (getting-started §7 "Using every CPU", limits; README; roadmap)
- [X] T009 Gates + quickstart validation + results; notes

## Implementation Notes (recorded during /speckit-implement)

- **Test-first observance.** T001 was written before the code and observed failing
  (`ferro: error: unrecognized arguments: --processes`). T004 was written after the supervisor
  existed: the database test passed at once; the orphan test passed at first and then failed
  in about 1 run in 3–8, which exposed the Windows accept problem below.
- **Problems found and fixed.**
  - The first green attempt timed out: the test reads the server's stderr only at the end, and
    300 access-log lines filled the Windows pipe buffer, blocking the workers' log writes. The
    test servers now run with `--log-level warning`.
  - **Windows accept quirk.** A worker sometimes never finished stopping. Python stack dumps
    showed every Python thread idle; engine logs showed the worker's acceptor never logging its
    stop; one incoming connection released it at once; sampling thread wait reasons showed the
    acceptor of one worker in a Winsock wait (reason 6) instead of its completion port
    (reason 15) — even before shutdown, and only transiently. So on a listening socket shared
    between processes a non-blocking `accept()` can block until the next connection, and an
    acceptor stuck there never sees the stop request. Plain-socket experiments ruled out the
    other explanations tried (blocking mode is per descriptor; making the supervisor's socket
    non-blocking did not help). Fix: while a worker with a shared listener waits for its engine
    to stop, it connects to its own port every 0.25 s (`ServerRunner.wake_acceptor`, Windows
    only); orphaned workers also exit outright `shutdown_timeout + 10 s` after the supervisor
    died. Result: 60 of 60 orphan shutdowns clean (previously about 1 in 3–8 hung), and
    `test_processes.py` green in 5 consecutive runs.
  - Worker spawn failures would have been reported as "could not bind": binding now raises a
    dedicated `BindError`.
  - The results generator printed `≥` to a cp1252 console and crashed after writing the file;
    its output is ASCII now. A hard-coded conclusion in the generated report ("processes pay off
    for database-bound handlers") was contradicted by the third run (0.84×); the sentence is
    computed from the measured ratios now.
  - Environment: with little free memory (5.9 GB machine, other desktop applications), rustc
    crashed once (`STATUS_STACK_BUFFER_OVERRUN`) and left corrupt incremental artifacts; builds
    ran with `CARGO_BUILD_JOBS=2` and incremental compilation off afterwards.
- **US3 profile and change** ([results](../../tests/benchmarks/results/010-bridge-queue-ab.md)):
  on the database route `call_soon_threadsafe` ran once per query result, each from a database
  thread taking the interpreter lock to write asyncio's self-pipe — more than half of the
  Python thread's time; on hello world the wake-ups were above the 5% bar. Database completions
  now join the bridge's batched queue and a drain keeps its flag while it works. Interleaved
  before/after (5 rounds, builds swapped per server start): database read **3.21×**
  (2,590 → 7,937 req/s, every round ≥ 2.37×), hello world **1.18×**, JSON route **1.08×**
  (medians of per-round ratios).
- **Results** ([results](../../tests/benchmarks/results/010-performance.md)). The machine's speed
  varied widely between runs (Rust-only baseline medians 22.7k, 26.3k and 13.4k req/s), so the
  criteria use ratios measured within one run. Three full runs:
  - **SC-001 PASS**: CPU-heavy route with 4 processes 3.09×, 2.92×, 3.06× of one process.
  - **SC-002 FAIL**: hello world with 4 processes 0.71×, 0.51×, 0.58× of one process; a sweep of
    other splits (2×4, 4×4, 4×1, 4×2 processes × workers) gave 0.62–0.73×. One process served
    hello world at 66–88% of the Rust-only baseline, so 1.3× would need 86–114% of what the
    engine alone serves with the client on the same machine; and extra processes add cost for
    near-empty handlers (most likely smaller batches per Python thread, so the fixed wake-up
    cost is shared by fewer requests). The target was set before the bridge change made hello
    world much less Python-bound. Documented: use processes for handlers that spend time in
    Python. Not pursued further.
  - **SC-003 PASS**: 0 failed requests across all suite runs; a worker killed 3 s into a 10 s
    load run (4 processes, 100 clients): 23 of 69,406 requests failed (0.033%; 0.043% in a
    separate check).
  - Reported: database read with 4 processes 1.36×, 1.11×, 0.84× of one; authenticated request
    (session + user lookup) 2.3–3.3k req/s — the next profiling target.
- **Gates:** ruff; `pytest` 626 passed, 2 skipped (affected suites re-run after the last
  change: 45 passed); `cargo clippy --workspace --all-targets -D warnings`; `cargo test` 150
  passed — Windows 11, CPython 3.13, PostgreSQL 18.1.
- **Not validated here:** the Linux/macOS paths (inherited descriptor via `pass_fds`, own
  process group, parent-pid polling) are implemented but were not run on this Windows machine.
- **Deferred:** shared-memory caches between processes; zero-downtime reloads.
