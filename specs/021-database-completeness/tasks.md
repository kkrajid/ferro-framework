---
description: "Task list for Feature 021 — Database Completeness"
---

# Tasks: Database Completeness

**Input**: [spec.md](spec.md) (clarifications record the answers to each gap found against
Django's database reference).

## Phase 1: Transactions and reading

- [X] T001 `TransactionCommand::BeginIsolated(Isolation)`; PostgreSQL keeps the pending BEGIN statement (pipelined with the first statement as in Feature 020); SQLite ignores the level (`crates/database`, `crates/postgres/src/worker.rs`, `crates/sqlite/src/worker.rs`)
- [X] T002 `Session.begin(isolation)`; `transaction(isolation=...)`, outermost block only, kept by the decorator form (`python/ferro/db.py`)
- [X] T003 Serialization failures (40001) and deadlocks (40P01) map to `DatabaseUnavailableError` (`crates/postgres/src/errors.rs`)
- [X] T004 `Query::with_pk_tiebreak`; `QuerySet.iterator(chunk_size)`: keyset on the primary key when unordered, positional chunks with a primary-key tie-breaker otherwise, one query for sliced/grouped querysets
- [X] T005 Tests: isolation snapshots (read committed vs repeatable read vs serializable), write skew under serializable, nested isolation refused, iterator ordering/values/prefetch/slices, deletes during iteration

## Phase 2: Keys, indexes, connections

- [X] T006 `compile::sync_sequence` (PostgreSQL `setval` to the table's highest key only when ahead of the sequence); `insert` with an explicit automatic key and `bulk_insert` with any explicit key run it in the same batch
- [X] T007 `_ferro.reset_sequences`, `db.reset_sequences()`, `ferro db sequence-reset`
- [X] T008 Pattern indexes: `ddl::pattern_index_sql` for single-column `String`/`Text` indexes on PostgreSQL in `create_tables` and migrations (CreateIndex, DropIndex, index renames, type changes in and out of text)
- [X] T009 PostgreSQL `max_lifetime` / `max_idle` (connections replaced before a job, never inside a transaction); SQLite `timeout`
- [X] T010 Tests: explicit keys (single, lower, bulk), URL validation, SQLite busy timeout, connection replacement (via `pg_stat_activity.backend_start`), pattern indexes in `create_tables` and in migration SQL

## Phase 3: Migrations

- [X] T011 `Operation::RunSql`, `RunPython`, `CreateExtension`, `DropExtension` in the engine (state unchanged; reverses; SQL per dialect) and the bridge dicts
- [X] T012 `ops.RunSQL`, `ops.RunPython` (+ `noop`), `ops.CreateExtension`, `ops.DropExtension`; a migration with RunPython may contain only data operations
- [X] T013 Runner: migrations with RunPython run in one `transaction()`, steps in order (reversed on rollback), history row last; `apply_migration(record=False)` and batches inside sessions
- [X] T014 Tests: apply/revert SQL+Python together, failure leaves nothing, irreversible steps, validation, extensions, pattern indexes through real migrate/rollback

## Phase 4: Documentation

- [X] T015 Models guide (connection parameters, iterator, isolation levels, PostgreSQL notes, faster test databases, backend table), migrations guide (data migrations), CLI reference, CHANGELOG

## Implementation Notes

- **Sequence sync is not atomic with concurrent inserts**: PostgreSQL has no compare-and-set
  for sequences. The statement only moves a sequence when the table's highest key is ahead of
  it, so the common case (explicit keys below the sequence) does nothing; when it does move it,
  a default-keyed insert running in the same microseconds could still take a key that the
  explicit insert already used. Django does not sync automatically at all
  (`sqlsequencereset`); `ferro db sequence-reset` covers bulk loads.
- **iterator() on ordered querysets uses OFFSET**: each chunk costs more as the offset grows,
  and rows inserted or deleted before the current position during iteration shift the window.
  Keyset pagination is used only for the primary-key order, where it is exact.
- **Pattern indexes for existing projects**: indexes created before this feature get no
  `_like` companion (migrations only record the plain index); renames and drops use
  `IF EXISTS`, so both kinds of index keep working.
- **RunPython uses the current models**, not historical ones as in Django: a data migration
  written against a field that a later migration removes fails when replayed on a fresh
  database. Documented: empty such migrations once every database ran them.
- **Serialization failures** were `OperationalError` before; they are now its subclass
  `DatabaseUnavailableError`, so existing `except OperationalError` code still catches them,
  and unhandled ones answer 503 instead of 500.
