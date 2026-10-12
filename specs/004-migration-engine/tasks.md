---
description: "Task list for Feature 004 — Migration Engine"
---

# Tasks: Migration Engine

**Tests**: Required; each phase's tests are written and observed failing first.

## Phase 1: Setup

- [X] T001 Add `crates/migrations` (`ferro-migrations`, depends on ferro-database, ferro-orm) to the workspace and to the bridge; create `python/ferro/migrations/` package skeleton

## Phase 2: Foundational (Rust engine)

### Tests (must fail first)

- [X] T002 [P] `crates/migrations/tests/state.rs`: replay builds tables/columns/indexes; invalid operations (missing table/column, duplicate) error; reverse of each operation; irreversible RemoveColumn detected
- [X] T003 [P] `crates/migrations/tests/diff.rs`: create/drop table, add/remove/alter column, index add/remove, operation order, rename warning, PK change error, no-op round trip (diff after replaying its own output is empty)
- [X] T004 [P] `crates/migrations/tests/sql.rs`: SQL for every operation on PostgreSQL and SQLite (rebuild sequence, bound back-fill parameters); CreateTable equals `ddl::create_table`

### Implementation

- [X] T005 `crates/orm/src/ddl.rs`: column-definition/table helpers usable from a table state
- [X] T006 `crates/migrations/src/{ops,state}.rs`
- [X] T007 `crates/migrations/src/diff.rs`
- [X] T008 `crates/migrations/src/{reverse,sql}.rs`
- [X] T009 `crates/postgres/src/worker.rs`: refresh statement cache and retry once on SQLSTATE 0A000
- [X] T010 `crates/python-bridge/src/migrations.rs`: dict ↔ Operation, `migration_diff`, `apply_migration` (forward/backward + history row, one batch), `migration_history`

**Checkpoint**: `cargo test --workspace` green

## Phase 3: US1 - First migration (P1) 🎯

- [X] T011 [US1] Tests `tests/python/migrations/test_workflow.py` (both databases): makemigrations writes 0001 with CreateTable/indexes (no DB needed), migrate creates the same schema as create-tables and records history, second run reports no changes / nothing to apply
- [X] T012 [US1] `python/ferro/migrations/{ops,loader,writer,commands}.py` (makemigrations, migrate)

## Phase 4: US2 - Evolve models (P1)

- [X] T013 [US2] Tests `tests/python/migrations/test_evolve.py` (both databases): every US2 scenario with rows preserved, default back-fill, missing-default error, rename warning + hand-written RenameColumn keeps data, `--name`, `--check`, `--empty`
- [X] T014 [US2] Implement gaps (writer value rendering, default post-processing, options)

## Phase 5: US3 - Rollback and status (P2)

- [X] T015 [US3] Tests `tests/python/migrations/test_rollback.py` (both databases): rollback one/steps/to/zero restores schema and data where possible, irreversible refusal without changes, status markers, history entry without file reported
- [X] T016 [US3] `rollback` and `status` commands

## Phase 6: US4 - Safety (P2)

- [X] T017 [US4] Tests `tests/python/migrations/test_safety.py` + `tests/integration/test_migrations_cli.py`: failing migration leaves nothing (both databases), SQLite rebuild keeps rows/indexes/sequence, create-tables refusal, server start warning, non-linear history error, CLI exit codes
- [X] T018 [US4] CLI wiring in `python/ferro/cli.py`, create-tables refusal, startup warning

## Phase 7: Polish

- [X] T019 [P] Docs (getting-started migrations section, README, roadmap), example project migrations
- [X] T020 Gates + quickstart validation; notes

## Implementation Notes (recorded during /speckit-implement)

- **Test-first observance.** Red observed for the foundation (T002–T004: the `ferro-migrations`
  crate did not exist), US1 (T011: `ferro.migrations` import error) and US2/US4 (missing-default
  error, `--check`/`--empty`, create-tables refusal, startup warning). The US3 rollback tests (T015)
  passed on first run because `rollback`/`status` were written together with `migrate` in T012
  (they share the history and reverse code); no failing state was observed for them.
- **Fixed during implementation:** SQLite table rebuilds reset `AUTOINCREMENT` to the highest
  remaining id — the rebuild now copies the old `sqlite_sequence` row. PostgreSQL cached statements
  failed with "cached plan must not change result type" after `ALTER TABLE` — the worker clears its
  cache and retries once; batches prepare statements inside the transaction so DDL earlier in the
  same batch is visible.
- **Design note:** back-fill defaults are resolved in Python from static field defaults (callable
  defaults are refused with a message asking for a constant), then bound as parameters.
- **Results:** 17 Rust migration tests; 40 Python migration tests (all except the SQLite-only
  sequence case run on both databases) plus 4 CLI tests. Quickstart validated on SQLite.
- **Gates:** fmt, clippy `-D warnings`, `cargo test` (106), ruff, `pytest` (465 passed, 1 skipped)
  — Windows 11, CPython 3.13, PostgreSQL 18.1.
- **Deferred:** foreign keys in migrations (Feature 005), data migrations (`RunPython`) and
  squashing (post-MVP).
