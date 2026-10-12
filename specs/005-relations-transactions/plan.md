# Implementation Plan: Relationships, Transactions & Aggregation

**Branch**: `005-relations-transactions` | **Date**: 2026-10-10 | **Spec**: [spec.md](spec.md)

## Summary

Relations (foreign key, one-to-one, many-to-many) are resolved and compiled in the Rust ORM: field
metadata carries the relation, the registry resolves reverse names and link tables, lookups across
relations become correlated `EXISTS` subqueries and widening operations (`select_related`, related
ordering, grouped values) become `LEFT JOIN`s. The query AST gains a condition tree (`Q`), field
expressions (`F`), aggregates and annotations. Transactions reserve one pooled connection through a
session job routed by a context variable. Migrations and `create-tables` learn foreign keys,
composite unique indexes and dependency ordering ([research.md](research.md)).

## Technical Context

**Language/Version**: Rust 1.99; Python ≥ 3.12 · **Dependencies**: `async-channel` moves into the
shared `ferro-database` crate (already used by the PostgreSQL backend); nothing else new
**Storage**: SQLite, PostgreSQL · **Testing**: `cargo test` (lookup parsing, SQL per dialect,
migration ops with references), pytest on both databases (relations, transactions, aggregates,
migrations), concurrency test for SC-003, benchmark rerun for SC-005

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| I | PASS | Lookup resolution, SQL, value conversion, transactions in Rust; Python wires descriptors/managers and orchestrates prefetch. |
| II | PASS | Driver types stay inside backends; session channel type is ours. |
| III | PASS | Relations are part of the owned ORM metadata. |
| IV | PASS | Identifiers from metadata; every value, F constant and default bound. |
| V | PASS | Cancellation and dropped sessions roll back; `PROTECT` enforced by the database. |
| VI | PASS | One job per query; transactions reuse one connection without blocking the event loop. |
| VII | PASS | Every new test parametrized over SQLite and PostgreSQL. |
| VIII | PASS | SC-002 query counts and SC-005 benchmark measured. |
| IX | PASS | Django names: `ForeignKey`, `related_name`, `select_related`, `Q`, `F`, `annotate`. |
| X | PASS | Roadmap Feature 005; exclusions listed in spec assumptions. |

## Project Structure

```text
crates/database/src/lib.rs           Job::{Session, Transaction, Schema}, SessionTask, session helpers
crates/orm/src/field.rs              Relation, OnDelete, attname
crates/orm/src/meta.rs               relation resolution, composite indexes
crates/orm/src/registry.rs           many-to-many, reverse relations, creation order
crates/orm/src/path.rs               lookup/path resolution across relations (new)
crates/orm/src/query.rs              Node tree, Expr, Aggregate, annotations, values, select_related
crates/orm/src/compile.rs            EXISTS/LEFT JOIN/aggregates/GROUP BY/ON CONFLICT
crates/orm/src/ddl.rs                REFERENCES, composite indexes, ordered creation
crates/sqlite/src/worker.rs          sessions, transaction control, schema job
crates/postgres/src/worker.rs        sessions, transaction control, schema job
crates/migrations/src/*              references, composite indexes, ordering, constraint renames
crates/python-bridge/src/orm.rs      relation specs, Q/F/aggregate conversion, values, select_related
crates/python-bridge/src/session.rs  Session pyclass, context variable, routing (new)
python/ferro/models/{fields,relations,base,queryset,expressions,exceptions}.py
python/ferro/db.py                   transaction()
python/ferro/migrations/ops.py       references / composite index rendering
tests/python/orm/test_relations*.py, test_transactions.py, test_aggregates.py, test_q_f.py
tests/python/migrations/test_relations.py
tests/benchmarks/run_005.py
```

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Prefetch orchestration in Python | It composes ordinary querysets per level and assigns caches on Python objects | A Rust implementation would rebuild the same objects through the same boundary with no fewer queries |
| Hidden link model classes | Many-to-many add/remove/query reuse the normal model machinery and migrations | A separate link-table code path in Rust, DDL and migrations would duplicate all of it |
