---
description: "Task list for Feature 003 — PostgreSQL Backend"
---

# Tasks: PostgreSQL Backend

**Tests**: Required (constitution VII). Each phase's tests are written and observed failing first.

## Phase 1: Setup

- [X] T001 Add `crates/postgres` (`ferro-postgres`) to the workspace with tokio-postgres, tokio-postgres-rustls, rustls (`ring`), webpki-roots, rustls-pki-types, async-channel, tokio (`rt-multi-thread`); depend on it from `crates/python-bridge`

## Phase 2: Foundational (shared changes)

### Tests (must fail first)

- [X] T002 [P] `crates/orm/tests/postgres_dialect.rs`: SQL + params for select/filter (every lookup incl. LIKE/ILIKE escaping)/order/window (incl. offset-only)/count/exists (aliased subquery)/insert/update/delete and DDL for every field kind with `PostgresDialect`; update `crates/orm/tests/compile.rs` for the subquery alias
- [X] T003 [P] `crates/postgres/tests/codec.rs`: numeric encode/decode round trips (0, negatives, 38 digits, fractional scales, trailing zeros), date/timestamp/time conversions at range edges, URL parsing (parameters, unknown parameter error, pool_size, sslmode values, no password in errors)
- [X] T004 [P] Update `crates/sqlite/tests/backend.rs` for `Batch { returning }` and `Value::Decimal` binding; add `MissingTable` expectation

### Implementation

- [X] T005 `crates/database`: `Value::Decimal`, `Job::Batch { statements, returning }`, `Outcome::Batch { affected, returned }`, `DbError::MissingTable`
- [X] T006 `crates/orm/src/compile.rs`: `PostgresDialect`, subquery alias, dialect hook for offset-only
- [X] T007 `crates/sqlite`: returning batches, Decimal binding, MissingTable mapping
- [X] T008 `crates/postgres`: config/URL parsing, TLS, worker pool with reconnect and statement cache, parameter encoding, cell decoding, numeric + date/time codecs, error mapping

**Checkpoint**: `cargo test --workspace` green

---

## Phase 3: User Story 1 - Same code on PostgreSQL (P1) 🎯

### Tests (must fail first)

- [X] T009 [US1] `tests/python/orm/conftest.py`: temporary PostgreSQL cluster (session), per-test schema, `database` fixture parametrized over sqlite/postgres; every existing ORM test now runs on both

### Implementation

- [X] T010 [US1] Bridge: scheme dispatch (`postgres://`, `postgresql://`) to `PgBackend` + `PostgresDialect`, `pool_size` override, `Value::Decimal` conversions, returning-based `bulk_insert`, `MissingTable` → `OperationalError` hint

---

## Phase 4: User Story 2 - Exact and native values (P1)

- [X] T011 [P] [US2] `tests/python/orm/test_postgres.py`: 38-digit decimal round trip, native column types via catalog, Unicode `icontains`/`iexact`, value comparisons on dates/UUIDs/decimals (write first, must fail where not yet supported)
- [X] T012 [US2] Fix any gaps found by T011 in the codecs/dialect

## Phase 5: User Story 3 - Production-ready connections (P2)

- [X] T013 [P] [US3] Extend `test_postgres.py`: `pool_size` cap observed in `pg_stat_activity`, server restart then successful query, `sslmode=require` refused by a non-TLS server, `sslmode=disable` works, unknown URL parameter error, password absent from connection errors
- [X] T014 [US3] Implement/adjust reconnect, TLS refusal message, URL errors to satisfy T013

## Phase 6: User Story 4 - Clear PostgreSQL errors (P2)

- [X] T015 [P] [US4] Extend `test_postgres.py` + `test_no_driver_leak.py`: unique/not-null/missing-table mapping parity, connection errors without driver names (`tokio_postgres`, `postgres_types`, `rustls`)
- [X] T016 [US4] Error-mapping fixes found by T015

## Phase 7: Polish

- [X] T017 [P] CI: PostgreSQL service + `FERRO_TEST_POSTGRES_URL` in `.github/workflows/ci.yml`
- [X] T018 [P] Docs: PostgreSQL section in `docs/getting-started.md`, README, roadmap status
- [X] T019 SC-003 load run against PostgreSQL (`tests/benchmarks/run_003.py`), results file
- [X] T020 Gates + quickstart validation; record outcomes

## Implementation Notes (recorded during /speckit-implement)

- **Test-first observance.** Red observed for the foundation (T002–T004: unresolved
  `PostgresDialect`, `Value::Decimal`, `Batch { returning }`) and for US1 (T009: every `[postgres]`
  case failed with "unsupported database URL scheme" on the previous build). The PostgreSQL-only
  tests of US2–US4 (T011/T013/T015) passed on first run because pooling, reconnect, TLS refusal,
  codecs and error mapping are backend internals built in T008; no failing state was observed for
  them.
- **Fixed during implementation:** `pg_ctl start` with captured output hangs on Windows (the server
  inherits the pipe) — the test helper now uses DEVNULL.
- **Contract change:** `QuerySet.sql()` now returns decimal parameters as `Decimal` (exact) instead
  of `float`; SQLite still stores decimals as REAL.
- **Results:** 274 ORM tests on both databases plus 16 PostgreSQL-only tests; SC-003: 0 failures,
  peak 10 connections with `pool_size=10` (`tests/benchmarks/results/003-postgresql-backend.md`).
- **Gates:** fmt, clippy `-D warnings`, `cargo test` (89), ruff, `pytest` (422) — Windows 11,
  CPython 3.13, PostgreSQL 18.1. The CI PostgreSQL job (ubuntu, postgres:17) has not run yet.
- **Deferred:** prepared-statement cache invalidation after schema changes (needed once migrations
  alter tables — Feature 004 handles it).
