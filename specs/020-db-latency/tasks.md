---
description: "Task list for Feature 020 — Database Latency of Transactions"
---

# Tasks: Database Latency of Transactions

**Input**: [spec.md](spec.md); approved plan (Feature 020 plan, 2026-10-20).

## Phase 1: Measure

- [X] T001 Per-query cost Ferro vs asyncpg on one PostgreSQL cluster (debug and release builds); server-path split into database-thread time (`ferro_db_query_duration_seconds`) and delivery
- [X] T002 `tests/benchmarks/run_020.py`: checkout under `oha` for any installed build and example; stops its whole process tree

## Phase 2: User Story 1 — no accidental slow builds

- [X] T003 `_ferro.BUILD_PROFILE`; `ferro run`/`ferro dev` warn on debug builds (`python/ferro/cli.py`); test `test_debug_builds_are_announced`
- [X] T004 README (building from source) and performance guide (measuring with a release build, `ferro run`; transactions on hot rows)

## Phase 3: User Story 2 — fewer round trips

- [X] T005 [P] Regression test `test_first_operations_of_a_transaction` (empty, failing first statement, savepoint first, batch first, lock first) on SQLite and PostgreSQL
- [X] T006 PostgreSQL sessions: BEGIN deferred and pipelined with the first statement; no-op transactions send nothing (`crates/postgres/src/worker.rs`, tokio `macros`)
- [X] T007 Checkout example inserts the order once with its total (`examples/ecommerce/app.py`)

## Phase 4: Results

- [X] T008 Interleaved before/after with release builds; event-loop profile; [results](../../tests/benchmarks/results/020-db-latency.md)

## Implementation Notes

- **The user's 32 ms vs 8.9 ms**: the largest factor we can reproduce is the build: a debug build
  (`maturin develop`) is 3–5× slower than the release wheel (checkout p50 244 ms vs 87 ms here),
  and the benchmark ran `ferro dev`. Whether the user's numbers came from a debug build is not
  confirmed yet.
- **Round trips**: BEGIN pipelining and the example change remove 2 of 7 round trips from the
  checkout transaction, but interleaved release runs showed no difference on this machine
  (39 ms / ~455 req/s both): the database is local, and the event-loop thread is saturated.
  They are kept because they are correct (tests above) and help when the database is remote;
  SC-001 is **not met** on this machine.
- **Bottleneck found**: about 40% of the event-loop thread's CPU under load is asyncio's Windows
  proactor handling wake-ups from Ferro's threads; the application and ORM code are a small part.
  A selector loop was not measurably better here. Next step (not done): fewer wake-ups per
  request or an optional faster loop (uvloop on Linux), measured on Linux.
- **Mistake during measurement**: cleaning up leftover benchmark servers by command-line pattern
  also stopped the user's own `ferro dev` server (another project); `run_020.py` now stops only
  its own process tree.
- **Test-first**: T005 was written first and passed on the old code too (a regression guard for
  the new BEGIN handling, which changes no visible behaviour); T003's test was written with the
  feature.
