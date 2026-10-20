# Feature Specification: Database Completeness

**Feature Branch**: `021-database-completeness`

**Created**: 2026-10-20

**Status**: Implemented

**Input**: "https://docs.djangoproject.com/en/6.1/ref/databases/ — read this; does my framework
need any update? … please solve all limitations". Compared with Django's database reference for
the two backends Ferro supports (PostgreSQL, SQLite).

## Clarifications

### Session 2026-10-20

Recommended answers recorded (the user asked to solve every limitation found):

- Q: Explicit primary keys on PostgreSQL? → A: Verified bug: `create(pk=1)` then `create()` raises
  IntegrityError because the identity sequence is not advanced. After inserting explicit values
  into an automatic primary key, Ferro advances the sequence when the value is ahead of it (never
  moves it backwards). A `ferro db sequence-reset` command repairs tables loaded by other tools.
- Q: Data migrations? → A: New operations `ops.RunSQL(sql, reverse_sql=None)` and
  `ops.RunPython(forward, backward=None)` (async callables run inside the migration's transaction),
  and `ops.CreateExtension(name)` (PostgreSQL; nothing on SQLite).
- Q: Isolation levels? → A: `transaction(isolation="repeatable read" | "serializable" |
  "read committed")`; SQLite transactions are already serializable, so the option is accepted and
  has no effect there. Only on the outermost block.
- Q: Large result sets? → A: `QuerySet.iterator(chunk_size=1000)` — an async iterator that fetches
  rows in chunks: keyset pagination on the primary key when the queryset is ordered by it (or not
  ordered), otherwise ordered chunks with the primary key as tie-breaker. No server-side cursor,
  so it works with PgBouncer in transaction mode.
- Q: Connection lifetime? → A: PostgreSQL URL parameters `max_lifetime` and `max_idle` (seconds):
  a pooled connection older or idler than that is replaced before its next job (never inside a
  transaction). Defaults: no limit (unchanged behaviour).
- Q: LIKE indexes? → A: On PostgreSQL, an index on a `String`/`Text` column also gets a
  `varchar_pattern_ops`/`text_pattern_ops` index so `startswith`/`contains`-style LIKE lookups can
  use it under non-C collations.
- Q: SQLite options? → A: URL parameter `timeout` (seconds, busy timeout; default 5).
- Q: Documentation? → A: Database notes in the models guide: PgBouncer modes, isolation and
  serialization failures, explicit primary keys, iterator, connection lifetime, SQLite limits,
  faster test databases.

## Requirements *(mandatory)*

- **FR-001**: Explicit primary-key inserts keep PostgreSQL identity sequences ahead;
  `ferro db sequence-reset` repairs all tables.
- **FR-002**: `RunSQL`, `RunPython`, `CreateExtension` migration operations, reversible when given
  a reverse.
- **FR-003**: `transaction(isolation=...)`.
- **FR-004**: `QuerySet.iterator(chunk_size=...)`.
- **FR-005**: PostgreSQL `max_lifetime` / `max_idle` URL parameters.
- **FR-006**: Pattern-ops indexes for text columns on PostgreSQL.
- **FR-007**: SQLite `timeout` URL parameter.
- **FR-008**: Documentation of the above and of backend differences.

## Success Criteria *(mandatory)*

- **SC-001**: Every requirement has tests on SQLite and PostgreSQL (where applicable); the full
  suite passes.
- **SC-002**: Existing projects' migrations and data keep working unchanged.
