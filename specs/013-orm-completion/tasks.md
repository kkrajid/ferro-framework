---
description: "Task list for Feature 013 — ORM Completion"
---

# Tasks: ORM Completion

**Input**: Design documents from `specs/013-orm-completion/` (plan, spec, research, data-model,
contracts, quickstart)

**Tests**: Required (constitution VII): tests written and observed failing before the
implementation; every database behaviour runs on SQLite and PostgreSQL.

**Format**: `[ID] [P?] [Story] Description`

## Phase 1: User Story 1 — Lock rows for update (P1) 🎯 MVP

- [X] T001 [P] [US1] SQL-text tests `crates/orm/tests/locking_sql.rs`: PostgreSQL `FOR UPDATE OF "product"` after `LIMIT`, `NOWAIT`, `SKIP LOCKED`, joins left unlocked, SQLite unchanged, `values()` keeps the lock, annotations refused both ways, `count`/`exists` never lock
- [X] T002 [P] [US1] Python tests `tests/python/orm/test_locking.py`: `TransactionError` outside a transaction, SQL per backend, misuse errors, nullable `select_related`, a second transaction waits for the first, `nowait`/`skip_locked` (PostgreSQL), SC-001 20 buyers / 10 items on a pooled database (plus lost updates without the lock on PostgreSQL)
- [X] T003 [US1] `Lock`, `Query.lock`, `for_update`, `is_locking` and the annotate check in `crates/orm/src/query.rs`; export from `crates/orm/src/lib.rs`
- [X] T004 [US1] `Dialect::lock_clause` (PostgreSQL override) and lock in `compile::select` in `crates/orm/src/compile.rs`
- [X] T005 [US1] `PyQuery.select_for_update`, `locking`, refusals in `count`/`exists`/`aggregate` in `crates/python-bridge/src/orm_query.rs`; stub in `python/ferro/_ferro.pyi`
- [X] T006 [US1] `TransactionError` in `python/ferro/models/exceptions.py` (exported by `ferro.models` and `ferro.db`); `QuerySet.select_for_update` and the transaction check in `QuerySet._run` (`python/ferro/models/queryset.py`)

## Phase 2: User Story 2 — Custom link models (P2)

- [X] T007 [P] [US2] Tests `tests/python/orm/test_through.py`: the custom model is the link (no automatic table), `add` with `through_defaults` and no duplicates, required link fields, both managers with `remove`/`set`/`clear`/`prefetch_related`/cascade, self-referential with `through_fields`, definition errors (missing key, ambiguous keys, `through_fields` without `through`) and recovery
- [X] T008 [US2] `ManyToMany(through=, through_fields=)`, `auto_created`, `link()`; `add(through_defaults=)`, `set(through_defaults=)`, `_link_values` in `python/ferro/models/relations.py`
- [X] T009 [US2] Deferred linking (`_PENDING_LINKS`, `_link_keys`, `_link_custom`) in `python/ferro/models/base.py`; `relation.link()` in `python/ferro/db.py` and `python/ferro/migrations/commands.py`
- [X] T010 [US2] Custom-through relations read-only in admin forms (`python/ferro/admin/forms.py`)

## Phase 3: User Story 3 — Abstract base models (P2)

- [X] T011 [P] [US3] Tests `tests/python/orm/test_inheritance.py`: field order and copies, replaced/removed fields, abstract models unusable and unregistered, concrete parent refused, two-level hierarchy with `%(class)s` reverse names, inherited many-to-many and `unique_together`, migrations (columns in order, no drift)
- [X] T012 [US3] `_abstract`, `_inherit`, `_options`, `_Abstract` in `python/ferro/models/base.py`; abstract check in `Model.__init__`

## Phase 4: Polish

- [X] T013 Documentation: `docs/getting-started.md` §9 (locking, link models, abstract models) and §13 limits; roadmap row 013 Implemented
- [X] T014 Gates: `cargo fmt`, `cargo clippy --workspace --all-targets -D warnings`, `cargo test --workspace`, `ruff check`, `ruff format --check`, full `pytest`

## Implementation Notes

- **Lock placement**: only `compile::select` appends the lock clause, so `count()`/`exists()` on a
  windowed query (which embed `select_into` in a subquery) never lock; they are refused in the
  native query anyway because they read no rows to lock.
- **`values()`**: the spec first refused `values()` on locking queries; implementation keeps the
  lock instead (PostgreSQL allows `SELECT col ... FOR UPDATE`, Django does too) and refuses
  `annotate()` (GROUP BY cannot lock). Spec FR-003 updated.
- **SQLite**: no clause. `test_second_transaction_waits_for_the_first` passes on SQLite because
  the second transaction's `BEGIN IMMEDIATE` waits; SC-001 runs on a file database with four
  connections. `nowait`/`skip_locked` are PostgreSQL-only behaviours (test skipped on SQLite with
  the reason).
- **SC-001 result**: 20 buyers / 10 items → exactly 10 sales, stock 0 on both databases; the same
  code without the lock on PostgreSQL sold more than 10 (lost updates), as expected.
- **Circular definitions**: `Band` (source) and `Membership` (link with a key to `Band`) depend on
  each other, so the relation is linked after both register (`_PENDING_LINKS`), not before the
  source registers. A failed link is removed from the pending list before raising, so later
  model definitions are unaffected (tested).
- **Duplicates**: automatic link tables keep `bulk_create(ignore_conflicts=True)` (unique pair
  index); custom link models may not be unique per pair, so `add()` selects existing pairs first
  (one extra query, only for custom links). Concurrent `add()` of the same pair without a unique
  constraint can still insert twice — declare `unique_together` on the link model to prevent it
  (documented in research R5).
- **Inheritance copies**: fields are shallow-copied and re-bound with `__set_name__`; foreign key
  targets and many-to-many link state are reset on the copy so each concrete model resolves its
  own. `table` is never inherited from a base `Meta` unless the subclass's own `Meta` inherits it.
