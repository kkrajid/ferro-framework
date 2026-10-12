---
description: "Task list for Feature 002 — ORM Foundation on SQLite"
---

# Tasks: ORM Foundation on SQLite

**Input**: Design documents from `/specs/002-orm-foundation/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: Required (constitution VII). Lesson from Feature 001: Rust layers are built and tested
first in the foundation phase; the *Python API* for each story is added only in that story's phase
so its tests are observed failing first.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 Add workspace members and dependencies: `crates/database` (`ferro-database`), `crates/orm` (`ferro-orm`), `crates/sqlite` (`ferro-sqlite`, rusqlite 0.40 `bundled`, crossbeam-channel 0.5); add them to `crates/python-bridge/Cargo.toml`
- [X] T002 [P] Create `python/ferro/models/` package skeleton (`__init__.py`) and `python/ferro/db.py` placeholder exporting nothing yet

---

## Phase 2: Foundational (Rust ORM core + bridge plumbing)

### Tests (write first, must fail)

- [X] T003 [P] `crates/orm/tests/meta.rs`: field/column identifier validation, single primary key, implicit auto `id`, reserved names, Decimal bounds, registry replace-on-redefine and table clash error
- [X] T004 [P] `crates/orm/tests/compile.rs`: every row of contracts/sql-compilation.md (SQL text + params), lookup parsing (`field__lookup`, unknown field/lookup errors listing options, applicability per kind), exclude NULL semantics, window composition, update/delete on window rejected, DDL for every field kind and option
- [X] T005 [P] `crates/sqlite/tests/backend.rs`: open `:memory:` and temp file (WAL, foreign_keys, busy_timeout applied), Query/Execute/Batch/ListTables outcomes, unique/not-null violations → `DbError::Integrity` with table+columns, batch rollback on failure, cancelled job skipped, unwritable path error, 200 concurrent jobs across the pool, URL parsing (relative, absolute, Windows drive, memory, bad scheme)

### Implementation

- [X] T006 [P] `crates/database/src/lib.rs`: `Value`, `Statement`, `Rows`, `Job`, `Outcome`, `DbError`, `Backend` trait (`submit(job, cancelled, done)`, `close`)
- [X] T007 `crates/orm/src/{field,meta,registry}.rs`: `FieldKind`, `FieldMeta`, `ModelMeta::new`, `Registry`
- [X] T008 `crates/orm/src/{query,lookup}.rs`: AST, lookup parsing, applicability, window composition
- [X] T009 `crates/orm/src/{dialect,compile,ddl}.rs`: `Dialect` trait, `SqliteDialect`, compiler for select/count/exists/insert/update/delete/row ops, DDL
- [X] T010 `crates/sqlite/src/{lib,url,worker,errors}.rs`: `SqliteBackend` (thread pool, bounded channel, pragmas), URL parsing, error mapping
- [X] T011 `crates/python-bridge/src/db.rs`: global database (backend + dialect), `db_connect`/`db_disconnect`/`db_is_connected`, future creation on the running loop and `Deliver` completion via `call_soon_threadsafe`
- [X] T012 `crates/python-bridge/src/orm_values.rs`: Python ↔ `Value` conversion and validation for every `FieldKind`, raising `FieldError`
- [X] T013 `crates/python-bridge/src/orm.rs`: `register_model`, `ModelMeta`, `Query` (filter/order_by/window/reverse/sql/fetch/count/exists/update/delete), `insert`, `update_instance`, `delete_instance`, `bulk_insert`, `bulk_update`, `create_tables`; rows → instances without per-row Python calls
- [X] T014 `python/ferro/models/exceptions.py` and `python/ferro/db.py` (`connect`, `disconnect`, `is_connected`, `create_tables`); `_ferro.pyi` additions

**Checkpoint**: `cargo test --workspace` green; Python can connect/disconnect

---

## Phase 3: User Story 1 - Declare a model and store records (P1) 🎯 MVP

### Tests (write first, must fail)

- [X] T015 [P] [US1] `tests/python/orm/conftest.py` (in-memory database per test, `use_models(*models)` helper creating tables) and `tests/python/orm/test_models_crud.py`: implicit `id`, defaults, create + get round trip with types, save update, delete, refresh, `pk`, repr, unknown kwarg error, `max_length`/None/wrong-type `FieldError` with nothing written, DoesNotExist/MultipleObjectsReturned from `get`, table name default and `Meta.table`, reserved field names rejected, table clash error

### Implementation

- [X] T016 [US1] `python/ferro/models/fields.py`: `Field` base + `String`, `Integer`, `BigInteger`, `Decimal`, `Boolean` (remaining kinds in US4)
- [X] T017 [US1] `python/ferro/models/base.py`: `ModelBase` metaclass (collect fields, snake_case table, `Meta`, reserved names, register via native, per-model exceptions), `Model` (`__init__` defaults, `pk`, `save`, `delete`, `refresh`, `__repr__`), `Manager`
- [X] T018 [US1] `python/ferro/models/queryset.py`: `QuerySet` with `all`, `__await__`, `get`, `create`; `python/ferro/models/__init__.py` exports

**Checkpoint**: vision §25 second milestone (`class User(models.Model)` … `await User.objects.all()`)

---

## Phase 4: User Story 2 - Query with filters, ordering and pagination (P1)

### Tests (write first, must fail)

- [X] T019 [P] [US2] `tests/python/orm/test_queries.py`: every lookup against seeded data, filter/exclude chaining, exclude keeps NULL rows, `field=None`, `in` (incl. empty), ordering incl. `-` and `pk`, unknown field/lookup/order errors, slicing/limit/offset/negative index error, count/exists/first/last (ordered and unordered), queryset immutability and re-await, `sql()` output
- [X] T020 [P] [US2] `tests/python/orm/test_injection.py` (SC-003): SQL-looking strings through every filter lookup, ordering names, create/update values, `in` lists; table intact and values stored literally; `%`, `_`, `*`, `?`, `[` matched literally

### Implementation

- [X] T021 [US2] Extend `QuerySet`: `filter`, `exclude`, `order_by`, `limit`, `offset`, slicing, `count`, `exists`, `first`, `last`, `sql`

**Checkpoint**: US1 + US2 work independently

---

## Phase 5: User Story 3 - Bulk and set-based writes (P2)

### Tests (write first, must fail)

- [X] T022 [P] [US3] `tests/python/orm/test_bulk.py`: `update()` count + effect, `delete()` count, all().delete(), `bulk_create` 500 objects with pks and atomic rollback on a unique violation, `bulk_update` writes only listed fields, unknown field / invalid value errors before writing, `batch_size`

### Implementation

- [X] T023 [US3] Extend `QuerySet` with `update`, `delete`, `bulk_create`, `bulk_update`

**Checkpoint**: US1–US3 work independently

---

## Phase 6: User Story 4 - The full field system (P2)

### Tests (write first, must fail)

- [X] T024 [P] [US4] `tests/python/orm/test_fields.py`: every field type round-trips edge values (SC-006), `null`, value and callable defaults, `unique` → `IntegrityError` naming model/field, `index` creates index, custom primary key (UUID with default), `column=` name, DateTime UTC storage and naive rejection, Time/Date validation, Decimal digits/places errors and quantized read-back, JSON non-serializable error, integer range errors

### Implementation

- [X] T025 [US4] Add `Text`, `Float`, `Date`, `DateTime`, `Time`, `UUID`, `JSON` to `python/ferro/models/fields.py` and exports

**Checkpoint**: US1–US4 work independently

---

## Phase 7: User Story 5 - ORM inside a running application (P2)

### Tests (write first, must fail)

- [X] T026 [P] [US5] `tests/python/orm/test_server_orm.py`: `App(database_url=...)` connects on start and disconnects on stop, handlers create/list/detail, `get_or_404` → 404 envelope, `to_dict()` JSON response, no-database error message, 50 concurrent list requests succeed while a fast route stays responsive
- [X] T027 [P] [US5] `tests/integration/test_db_cli.py`: `ferro db create-tables` created/exists lines, `--database-url` vs `FERRO_DATABASE_URL` precedence, missing URL → exit 2, bad target → exit 2, unwritable path → exit 1

### Implementation

- [X] T028 [US5] Add `database_url` setting (`config.py`, CLI `--database-url` on dev/run/db, `App(database_url=...)`), connect/disconnect in `ServerRunner`
- [X] T029 [US5] `ferro db create-tables` subcommand in `python/ferro/cli.py`
- [X] T030 [US5] `get_or_404` on `QuerySet`, `Model.to_dict()`
- [X] T031 [P] [US5] `examples/products_api/app.py`: CRUD API using the ORM

**Checkpoint**: All stories independently functional

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T032 [P] FR-024 test `tests/python/orm/test_no_driver_leak.py`: error messages and logs never mention `rusqlite`, `libsqlite3`, `crossbeam`, `pyo3`
- [X] T033 [P] `tests/benchmarks/run_002.py` + results `tests/benchmarks/results/002-orm-foundation.md` (SC-004, SC-005)
- [X] T034 [P] Docs: ORM section in `docs/getting-started.md`, README feature list
- [X] T035 Run all gates and quickstart.md validation; record outcomes
- [X] T036 Update `docs/roadmap.md` (002 implemented; aggregation moved to 003 scope)

---

## Dependencies & Execution Order

- Setup → Foundational → US1 → (US2, US3, US4 in any order) → US5 → Polish.
- Within Foundational: T006 → T007 → T008 → T009; T006 → T010; T009 + T010 → T011 → T012 → T013 → T014.
- US2–US4 depend only on US1's model layer; US5 depends on US1 (and uses US2 for listing).

## Parallel Opportunities

T003/T004/T005 together; T006 with T002; each story's test tasks are [P] with other stories' tests.

## Implementation Strategy

MVP = Setup + Foundational + US1 (second vision milestone). Then US2 (queries), US3 (bulk), US4
(fields), US5 (web integration), Polish.

## Implementation Notes (recorded during /speckit-implement)

- **Test-first observance.** Red observed before implementation for every phase: Rust ORM core
  (T003/T004 → T007–T009), SQLite backend (T005 → T010), US1 (T015), US2 (T019/T020), US3 (T022),
  US4 (T024), US5 (T026/T027). The 12 injection cases that push payloads through `update()` stayed
  red until US3 delivered `QuerySet.update()`. Exception: the `pk=` keyword fix in `Model.__init__`
  got its regression test written alongside the fix.
- **Measurements (T033):** 1,000-row read 4.0 ms; `bulk_create` 10,000 rows 0.137 s; 100-client list
  endpoint 0 failures (1,973 req/s) with a DB-backed detail route p99 48.7 ms (target < 50 ms;
  close to the limit). Each query attaches a database thread to the interpreter once to schedule
  delivery; batching deliveries per loop is the next optimization (Feature 010).
- **Gates at completion:** fmt, clippy `-D warnings`, `cargo test` (79), ruff, `pytest` (291) all
  pass on Windows 11 / CPython 3.13; quickstart Scenarios 1–2 validated by hand.
- **Deferred:** interrupting a running SQLite query when its awaiting task is cancelled; garbage-
  collector traversal for the native `ModelMeta` → class reference (redefined test models are
  kept alive).
