# Implementation Plan: ORM Completion

**Branch**: `013-orm-completion` | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/013-orm-completion/spec.md`

## Summary

Three ORM gaps close: `select_for_update()` (a `lock` on the native `Query`, compiled to
`FOR UPDATE OF <table> [NOWAIT|SKIP LOCKED]` on PostgreSQL and nothing on SQLite, refused outside
a transaction), many-to-many relations through a developer's link model (`through=`,
`through_fields=`, `through_defaults=` on `add`/`set`), and abstract base models
(`Meta.abstract = True`) with copied fields, `%(class)s` reverse names and inherited `Meta`
options ([research.md](research.md)).

## Technical Context

**Language/Version**: Rust 1.99 (edition 2024); Python ≥ 3.12 (dev on CPython 3.13)
**Primary Dependencies**: none new
**Storage**: SQLite and PostgreSQL (existing backends)
**Testing**: `cargo test -p ferro-orm` (SQL text: `crates/orm/tests/locking_sql.rs`), `pytest`
on both databases (`tests/python/orm/test_locking.py`, `test_through.py`, `test_inheritance.py`)
**Target Platform**: Windows 11 (development), Linux/macOS (CI)
**Project Type**: framework (Rust engine + Python package)
**Performance Goals**: no change to queries that do not lock; model definition cost unchanged
for models without abstract parents
**Constraints**: SQL text only in Rust; no hidden queries; migrations unchanged in format
**Scale/Scope**: 8 functional requirements, 3 user stories

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| I Thin Python, Rust core | PASS | Lock clause compiled in Rust; Python adds model-definition logic (metaclass) and manager orchestration, as before. |
| II Engine encapsulation | PASS | No engine types involved. |
| III Framework-owned ORM | PASS | Extends Ferro's own ORM; no third-party ORM. |
| IV Safe SQL | PASS | Lock clause is fixed text plus the quoted table name; values stay bound. |
| V Secure by default | PASS | Locking outside a transaction is an error, not a silent no-op; custom-through relations are read-only in the admin. |
| VI Coarse boundary, non-blocking | PASS | One statement per call as before; lock waits happen on database connections, not the Python thread. |
| VII Test-first + backend parity | PASS | Tests written first; all run on SQLite and PostgreSQL (SC-001 concurrency on a pooled file database and PostgreSQL). |
| VIII Measured performance | PASS | Non-locking paths unchanged (one `Option` check); no benchmark needed. |
| IX Django-familiar | PASS | Same names and semantics as Django (`select_for_update(nowait, skip_locked)`, `through`, `through_fields`, `through_defaults`, `Meta.abstract`, `%(class)s`). |
| X Scope discipline | PASS | Roadmap 013; `of=`, multi-table and proxy inheritance, symmetrical relations deferred. |

## Project Structure

```text
crates/orm/src/{query.rs (Lock, Query.lock, for_update, annotate check), compile.rs (Dialect::lock_clause, select), lib.rs}
crates/orm/tests/locking_sql.rs
crates/python-bridge/src/orm_query.rs (select_for_update, locking, refusals)
python/ferro/_ferro.pyi
python/ferro/models/{exceptions.py (TransactionError), queryset.py, relations.py, base.py, __init__.py}
python/ferro/{db.py, migrations/commands.py, admin/forms.py}
tests/python/orm/{test_locking.py, test_through.py, test_inheritance.py}
docs/getting-started.md
```

**Structure Decision**: existing layout; no new modules.

## Complexity Tracking

None.
