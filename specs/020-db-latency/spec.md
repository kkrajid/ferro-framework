# Feature Specification: Database Latency of Transactions

**Feature Branch**: `020-db-latency`

**Created**: 2026-10-20

**Status**: Implemented (see [tasks.md](tasks.md), Implementation Notes)

**Input**: A user's benchmark of the e-commerce checkout (`POST /api/orders`: row lock, stock
update, order lines in one transaction) measured p50 32.06 ms for Ferro against 8.93 ms for
FastAPI + asyncpg. "Solve this in a production-standard way."

## Clarifications

### Session 2026-10-20

- Q: What made Ferro slower? → A: Measured on one machine and PostgreSQL: (1) an unoptimized
  debug build of the native module (`maturin develop`) is 3–5× slower than the release wheel
  (one ORM query 4.3 ms vs 0.8 ms; checkout 244 ms vs 87 ms p50 at 20 connections) and nothing
  warned about it; the benchmark also ran `ferro dev`; (2) every database round trip inside the
  checkout transaction holds the product row's lock, so throughput on that hot row is bounded by
  the transaction's duration — the example used 7 round trips where 5 suffice; (3) in a release
  build each query still costs ~0.2–0.3 ms more than asyncpg (the hop to the database thread and
  back).
- Q: Cache logins to save queries? → A: No: authentication already loads session and user in one
  query, and a cache would need cross-process invalidation.
- Q: Pipelining — safe? → A: Only Ferro's own `BEGIN` is pipelined with the transaction's first
  statement; BEGIN on an idle connection fails only if the connection fails, in which case the
  statement after it fails too.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - No accidental slow builds (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a debug build of the native module, **When** `ferro run` or `ferro dev` starts,
   **Then** a warning names the build and how to get an optimized one; a release build prints
   nothing.
2. **Given** the performance guide and README, **Then** they say to measure with a release build
   and `ferro run`.

### User Story 2 - Fewer round trips per transaction (Priority: P1)

**Acceptance Scenarios**:

1. **Given** PostgreSQL, **When** a transaction runs statements, **Then** BEGIN travels with the
   first statement (one round trip), and a transaction without statements sends nothing.
2. **Given** a failing first statement, a savepoint, a batch or a row lock as the first
   operation, **Then** commit/rollback behave exactly as before (SQLite and PostgreSQL).
3. **Given** the e-commerce checkout, **Then** it writes each row once (no create-then-save).

### User Story 3 - Measured (Priority: P1)

**Acceptance Scenarios**:

1. **Given** `tests/benchmarks/run_020.py`, **Then** it reports the checkout's p50/p99/req/s and
   one query's p50 for any installed build, and leaves no process running.

## Requirements *(mandatory)*

- **FR-001**: The native module exposes its build profile; serving commands warn on debug builds.
- **FR-002**: PostgreSQL sessions defer BEGIN to the first statement and pipeline it; no-op
  transactions send nothing; behaviour is otherwise unchanged.
- **FR-003**: The checkout example avoids redundant writes inside its transaction.
- **FR-004**: A reproducible checkout benchmark and documented results; documentation for
  measuring performance and for transactions on hot rows.

## Success Criteria *(mandatory)*

- **SC-001**: Release build, same machine: checkout p50 lower and throughput higher than before
  the feature (interleaved runs).
- **SC-002**: No change in behaviour: the full test suite passes on SQLite and PostgreSQL.
- **SC-003**: Debug builds are announced every time a server starts.

## Assumptions

- The remaining per-query bridge cost in release builds is measured and recorded; replacing the
  wake-up mechanism is done only if the measurement shows it is the dominant cost.
