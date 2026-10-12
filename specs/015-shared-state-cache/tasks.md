---
description: "Task list for Feature 015 — Shared State Across Processes and a Cache API"
---

# Tasks: Shared State Across Processes and a Cache API

**Input**: Design documents from `specs/015-shared-state-cache/` (plan, spec, research,
contracts)

**Tests**: Required (constitution VII), written before the implementation; the database cache
runs on SQLite and PostgreSQL.

**Format**: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [X] T001 Crate `crates/shared` (`ferro-shared`, `memmap2`) added to the workspace; `ferro-core` and the bridge depend on it

## Phase 2: User Story 1 — Rate limits across processes (P1) 🎯 MVP

- [X] T002 [P] [US1] Tests `crates/shared/tests/rate.rs`: token-bucket equivalence (burst, retry-after, refill), separately opened handles share budgets, 8 threads never exceed a budget, 5,000 clients fit, foreign files refused, no overwrite
- [X] T003 [US1] `crates/shared/src/lib.rs`: file layout, `create`, `RateTable::open/check/check_at/occupied_at` (GCRA, per-run secret, 8 probes)
- [X] T004 [US1] `Limiter::shared` with fallback and a one-time warning (`crates/core/src/security/ratelimit.rs`); `shared_state` setting opens the table in `Policy::new` (`crates/core/src/security/mod.rs`, `crates/core/src/settings.rs`)
- [X] T005 [US1] Supervisor creates the private directory and `rates.shm` (`_ferro.create_shared_state`), passes `--shared-dir` to workers, removes it on exit (`python/ferro/cli.py`); workers add the native settings (`python/ferro/server.py`)
- [X] T006 [US1] Integration test `tests/integration/test_shared_state.py::test_rate_limits_hold_across_processes` (4 processes, 40 requests on fresh connections → exactly 10)

## Phase 3: User Story 2 — Metrics for the whole server (P1)

- [X] T007 [P] [US2] Unit tests in `crates/http/src/snapshots.rs`: summing, series only other workers have, dead workers' gauges dropped and counters kept, file round trip
- [X] T008 [US2] `crates/http/src/snapshots.rs` (publish, merged, merge rules); `Metrics::respond`; publisher thread with a final snapshot at stop (`crates/http/src/server.rs`); `metrics_dir` setting
- [X] T009 [US2] Integration test `test_metrics_cover_every_process` (400 requests over 4 processes = totals from any worker; a killed worker's counts stay)

## Phase 4: User Story 3 — Cache API (P2)

- [X] T010 [P] [US3] Tests `tests/python/test_cache.py`: full API on memory and database backends, LRU eviction, copies, expiry cleanup, 50 concurrent `incr`, `configure`, sharing between two processes
- [X] T011 [US3] `python/ferro/cache/__init__.py` (API, `MemoryCache`, `DatabaseCache`, proxy, `configure`, `apply_setting`) and `python/ferro/cache/models.py` (`CacheEntry`, `ferro_cache`)
- [X] T012 [US3] `cache` setting (`python/ferro/config.py`), `App(cache=...)` registering the model (`python/ferro/app.py`), applied at server start (`python/ferro/server.py`)

## Phase 5: Polish

- [X] T013 Documentation: `docs/getting-started.md` (processes, settings table, caching, limits); roadmap row 015
- [X] T014 Gates: fmt, clippy, cargo test, ruff, full pytest

## Implementation Notes

- **Test-first order**: the shared-table tests, the snapshot unit tests, the cache tests and the
  integration test were written before the code they exercise was wired in; the integration test
  was kept out of the tree while the 013/014 gate ran (it could only fail then).
- **Rate table**: 65,536 slots × 8 bytes; GCRA in one CAS. The first test version expected a
  slot to be free one period after the last request; with GCRA a client's slot frees when its
  arrival time passes (up to one emission interval later) — the test was corrected, not the code.
  The many-clients test tolerates up to 3 of 5,000 clients merged by 16-bit fingerprint
  collisions (expected about one in twenty runs at that load; merging only makes limits
  stricter).
- **Measured** (`tests/integration/test_shared_state.py`, Windows, 4 processes): 40 requests on
  fresh connections spread over the workers → exactly 10 × 200 and 30 × 429 (SC-001); 400
  requests → every scrape from any worker reports the same total, and the total stays after a
  worker is killed (SC-002). The first run of the metrics test read its baseline before the
  warm-up request's worker had published (off by one); the test now waits one publish interval
  first.
- **Cache**: database `incr` uses `select_for_update()` from Feature 013; 50 concurrent increments
  give exactly +50 on SQLite and PostgreSQL (SC-003). `get_or_set` uses `add`, so a racing
  process's value wins and is returned.
- **Unsafe code**: confined to `ferro-shared` (two `unsafe` blocks plus `Send`/`Sync` impls, each
  with a SAFETY comment); the file is created 0600 inside a `mkdtemp` (0700) directory.
- **Gates**: see Feature 017's notes (one combined run for 015–017: Rust all green; pytest 754
  passed, 3 skipped, 1 timing flake that passed on 6 reruns).
