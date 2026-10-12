# Implementation Plan: ORM Foundation on SQLite

**Branch**: `002-orm-foundation` | **Date**: 2026-10-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-orm-foundation/spec.md`

## Summary

Ferro's own ORM, first on SQLite. Python declares models (`ferro.models.Model` with field option
objects) and calls a Django-style async `QuerySet`; everything else is Rust: the model registry,
lookup parsing and value conversion at `filter()` time, an immutable query AST, a dialect-driven
SQL compiler that always binds parameters, DDL for `create-tables`, and a SQLite backend running
on its own thread pool so neither the event loop nor HTTP workers block. Results come back to the
event loop once per query and become model instances without per-row Python calls
([research.md](research.md) R1–R10).

## Technical Context

**Language/Version**: Rust 1.99 (edition 2024); Python ≥ 3.12

**Primary Dependencies**: rusqlite 0.40 (`bundled`, SQLite 3.53.2), crossbeam-channel 0.5,
existing pyo3 0.29 / serde_json; no ORM or query-builder crates (constitution III)

**Storage**: SQLite (file or in-memory)

**Testing**: `cargo test` for compiler SQL text + parameters (`crates/orm`), backend behavior
(`crates/sqlite`); `pytest` for the Python API, round-trips, injection cases, CLI and server
integration; `oha` for SC-005

**Target Platform**: Windows, Linux, macOS

**Project Type**: Library + CLI (extends Feature 001)

**Performance Goals**: 1,000-row read < 50 ms; `bulk_create` 10,000 rows < 1 s (SC-004);
100-client list endpoint with zero failures and p99 < 50 ms on a non-DB route (SC-005)

**Constraints**: one submit and one delivery crossing per query; no SQL text built from values;
DB work never on the Python execution thread

**Scale/Scope**: 3 new crates (~2.5k lines Rust), 4 Python modules, ~120 new tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|---|---|---|
| I. Thin Python, Rust Core | PASS | Lookup parsing, conversion, AST, SQL, execution, row→instance in Rust (R1, R10). Python: metaclass, field option objects, QuerySet wrapper, defaults. |
| II. Engine Encapsulation | PASS | Driver only inside `crates/sqlite`; errors re-expressed as `DatabaseError` family (R8); FR-024 test greps messages. |
| III. Framework-Owned ORM | PASS | No ORM/query-builder dependency; Model → AST → compiler → backend (R2). |
| IV. Safe SQL by Construction | PASS | Values only as parameters; identifiers from validated metadata, quoted; compiler tests assert SQL + params; injection test suite (SC-003). |
| V. Secure by Default | PASS | Foreign keys on, busy timeout, raw SQL API absent, no driver details in errors. |
| VI. Coarse Boundary, Non-Blocking | PASS | One submit + one delivery per query; DB threads; bounded job channel; rows → instances in one pass. |
| VII. Test-First with Backend Parity | PASS | Tests precede each story; parity is trivially met with one backend, and the backend-generic test suite is structured so Feature 004 runs it against PostgreSQL. |
| VIII. Measured Performance | PASS | SC-004/SC-005 measured; results recorded. |
| IX. Django-Familiar DX | PASS | `objects`, `filter(f__lookup=)`, `DoesNotExist`, `Meta.table`, `get_or_404`. |
| X. Scope Discipline | PASS | Relations, transactions, aggregation, PostgreSQL, migrations explicitly deferred. |

**Post-design re-check**: PASS (contracts keep driver and SQL internals private; `Query.sql()` is a
read-only debugging/testing aid that returns text and parameters separately).

## Project Structure

### Documentation (this feature)

```text
specs/002-orm-foundation/
├── plan.md  research.md  data-model.md  quickstart.md
├── contracts/
│   ├── python-orm-api.md      # ferro.models, ferro.db public API
│   ├── sql-compilation.md     # SQL produced per operation (SQLite dialect)
│   ├── cli-db.md              # ferro db create-tables, --database-url
│   └── rust-python-orm.md     # internal native surface
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
crates/
├── database/src/        # ferro-database: Value, Statement, Rows, DbError, Dialect, Backend
├── orm/src/             # ferro-orm: fields, model meta + registry, query AST, lookups, compiler, ddl
│   └── tests/           # SQL text + parameter tests
├── sqlite/src/          # ferro-sqlite: SqliteDialect, SqliteBackend (rusqlite + thread pool), url
│   └── tests/           # backend tests against :memory: and temp files
└── python-bridge/src/
    ├── orm.rs           # register_model, ModelMeta, Query pyclasses, execution functions
    ├── orm_values.rs    # Python ↔ Value conversion per FieldKind
    └── db.rs            # global database handle, connect/disconnect, futures + delivery
python/ferro/
├── models/__init__.py   # Model, fields re-export, exceptions
├── models/fields.py     # String, Integer, ... option objects
├── models/base.py       # ModelBase metaclass, Model, Manager
├── models/queryset.py   # QuerySet wrapper
├── models/exceptions.py # FieldError, DatabaseError, IntegrityError, OperationalError, ...
└── db.py                # connect, disconnect, create_tables, is_connected
examples/products_api/app.py
tests/python/orm/        # pytest suites per story
tests/integration/test_db_cli.py, test_orm_server.py
tests/benchmarks/run_002.py
```

**Structure Decision**: three new crates mirroring the vision layout; Python ORM as a
`ferro.models` package; `ferro.db` for connection lifecycle.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Defaults applied in Python (`Model.__init__`) | Defaults are arbitrary Python callables (`uuid.uuid4`) evaluated at instance creation | Calling them from Rust is the same Python call with an extra crossing |
