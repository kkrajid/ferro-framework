# Research: ORM Completion

## R1 — Where the lock lives

**Decision**: `Query` gains `lock: Option<Lock>` (`Wait`, `NoWait`, `SkipLocked`); only
`compile::select` appends the dialect's `lock_clause` after the window. `count`/`exists` (which
reuse `select_into` for windowed queries) never lock.

**Rationale**: SQL text stays in Rust (constitution I, IV); the clause depends on the dialect;
counting statements read no rows to lock, and PostgreSQL rejects `FOR UPDATE` with aggregates.

**Alternatives**: appending text in Python (breaks the "SQL only in the core" rule); locking in
`select_into` (would put `FOR UPDATE` inside `COUNT(*)` subqueries).

## R2 — PostgreSQL clause

**Decision**: `FOR UPDATE OF "<table>"` + `NOWAIT` / `SKIP LOCKED`, after `LIMIT/OFFSET`.

**Rationale**: `OF` restricts locks to the queried model, which is what the developer asked for and
is required when `select_related` produces LEFT JOINs (PostgreSQL refuses to lock the nullable
side of an outer join). The root table is never aliased in Ferro's SQL, so the table name is the
valid `OF` target. Lock-not-available (`55P03`, from `NOWAIT`) already maps to
`DatabaseUnavailableError` (Feature 011).

## R3 — SQLite

**Decision**: no clause; `select_for_update()` is accepted on SQLite.

**Rationale**: SQLite has no row locks and no `FOR UPDATE` syntax. Ferro's SQLite transactions
start with `BEGIN IMMEDIATE`, holding the single write lock for the whole transaction, so no other
transaction can change the rows before commit — the guarantee `select_for_update()` gives. Code
written for PostgreSQL runs unchanged on SQLite (constitution VII parity of behaviour).

## R4 — Outside a transaction

**Decision**: `QuerySet._run` raises `TransactionError` when the query is locking and no active
session is in the context. Checked in Python where the transaction context variable lives;
`count/exists/aggregate` refuse locking queries in the native query object.

**Rationale**: Outside a transaction (autocommit) PostgreSQL releases the lock at the end of the
statement, silently giving no protection; Django raises the same way.

## R5 — Custom link models

**Decision**: `ManyToMany(to, through=Model|"Name", through_fields=None)`. The Rust registry
already models many-to-many relations as `(through, from_field, to_field)`; only Python changes:
the automatic link model is skipped and the relation is registered once the link model exists
(`_PENDING_LINKS`, processed with pending models). Keys are found by target (one per side; for
`"self"` exactly two, in declaration order) or named with `through_fields`.

**Rationale**: The source model and its link model refer to each other (the link's foreign keys
need the source registered), so the link cannot be required before the source registers.

**`add()`**: the automatic link table has a unique pair index, so `bulk_create(ignore_conflicts=True)`
remains; a custom link model need not be unique per pair, so `add()` reads the existing pairs and
inserts only the missing ones (declare `unique_together` on the link model to make concurrent
`add()` calls of the same pair safe). Required extra fields (not null, no default) must be in
`through_defaults` — checked before any query so `set()` fails before changing anything.

**Admin**: such relations are read-only on the source's form; link rows are edited on the link
model's own page (Django's behaviour).

## R6 — Abstract inheritance

**Decision**: `Meta.abstract = True` (read from the class's own `Meta` only, so subclasses are
concrete unless they say otherwise). The metaclass merges fields of abstract parents (in base
order, parents first), then the class's own declarations; inherited fields and relations are
shallow-copied and re-bound (`__set_name__`) so each model owns its field objects (Django's
behaviour). `%(class)s` in `related_name` becomes the snake_case model name. A model without its
own `Meta` uses its base's (`unique_together`); `table` is only taken from the model's own `Meta`.
Concrete parents raise `FieldError` (multi-table/proxy inheritance stay out of scope).
`ManyToMany(through=...)` on an abstract model is refused (one link model cannot point at every
subclass).

**Alternatives**: multi-table inheritance (implicit one-to-one joins on every query; large ORM
change, rarely the right choice) — deferred.

## R7 — Migrations

No change needed: the autodetector diffs registered models; abstract models are never
registered; custom link models are ordinary models; automatic link models are not created for
`through=` relations. Tests confirm table and column order (`id`, inherited, own).
