# Implementation Plan: Migration Engine

**Branch**: `004-migration-engine` | **Date**: 2026-10-10 | **Spec**: [spec.md](spec.md)

## Summary

Django-style migrations: `crates/migrations` replays operations into a schema state, diffs it
against registered models, reverses operations and compiles them to dialect-specific SQL (ALTER on
PostgreSQL, ALTER or table rebuild on SQLite). `ferro.migrations` (Python) loads and writes
migration files and implements `makemigrations`, `migrate`, `rollback`, `migration status`.
Each migration runs atomically with its history row ([research.md](research.md)).

## Technical Context

**Language/Version**: Rust 1.99; Python ≥ 3.12 · **Dependencies**: none new
**Storage**: SQLite, PostgreSQL · **Testing**: `cargo test` (state/diff/reverse/SQL per dialect),
pytest on both databases (apply/rollback/data preservation/atomicity), CLI subprocess tests

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| I | PASS | State, diff, reverse, SQL in Rust; Python handles files and commands. |
| II | PASS | No engine/driver types involved. |
| III | PASS | Owned migration engine on the owned ORM metadata. |
| IV | PASS | Identifiers quoted from metadata; back-fill values bound (`UPDATE ... = $1`, `SELECT ?`). |
| V | PASS | Irreversible rollbacks refused up front; atomic migrations. |
| VI | PASS | One batch job per migration. |
| VII | PASS | Every migration test runs on SQLite and PostgreSQL. |
| VIII | PASS | No performance claims. |
| IX | PASS | Django command names and file style. |
| X | PASS | Roadmap Feature 004; FK operations deferred to 005. |

## Project Structure

```text
crates/migrations/src/{lib,ops,state,diff,reverse,sql}.rs
crates/migrations/tests/{state,diff,sql_sqlite,sql_postgres}.rs
crates/orm/src/ddl.rs                    column-definition helper shared with migrations
crates/postgres/src/worker.rs            statement-cache refresh on 0A000
crates/python-bridge/src/migrations.rs   dict ↔ Operation, diff, apply, history
python/ferro/migrations/{__init__,ops,loader,writer,commands}.py
python/ferro/cli.py                      makemigrations / migrate / rollback / migration status
tests/python/migrations/                 engine tests on both databases
tests/integration/test_migrations_cli.py
```

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Back-fill defaults resolved in Python | Defaults are Python values from field declarations and are written into migration files as Python literals | Converting in Rust would need the same Python objects back |
