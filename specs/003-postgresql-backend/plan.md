# Implementation Plan: PostgreSQL Backend

**Branch**: `003-postgresql-backend` | **Date**: 2026-10-10 | **Spec**: [spec.md](spec.md)

## Summary

Add `crates/postgres` (tokio-postgres + rustls) implementing the existing `Backend` trait, a
`PostgresDialect` in the ORM compiler, one new value variant (`Value::Decimal`) for exact decimals,
returning-based batches for bulk inserts, a missing-table error variant, and URL-scheme dispatch in
the bridge. The Feature 002 ORM suite becomes backend-parametrized and runs on a temporary local
PostgreSQL cluster ([research.md](research.md)).

## Technical Context

**Language/Version**: Rust 1.99; Python ≥ 3.12

**Primary Dependencies**: tokio-postgres 0.7.18, tokio-postgres-rustls 0.14, rustls 0.23 (`ring`),
webpki-roots 1, rustls-pki-types 1, async-channel 2, tokio (`rt-multi-thread`)

**Storage**: PostgreSQL ≥ 13 (tested on 18.1 locally), SQLite unchanged

**Testing**: `cargo test` (numeric/date codecs, dialect SQL, URL parsing), pytest suite on both
databases, PostgreSQL-specific pytest (`tests/python/orm/test_postgres.py`)

**Constraints**: no password in any error/log; ORM core and Python layer stay database-independent

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| I | PASS | All PostgreSQL logic in Rust crates. |
| II | PASS | Driver/TLS crates only in `crates/postgres`; errors re-expressed (R8). |
| III | PASS | No ORM dependency; dialect added to the owned compiler. |
| IV | PASS | `$n` parameters only; LIKE patterns escaped; compiler tests per dialect. |
| V | PASS | TLS verified by default when used; secrets never echoed (R7). |
| VI | PASS | Async driver on its own runtime; bounded channel; Python thread never blocks. |
| VII | PASS | Same ORM suite runs on both databases (R9) — the parity rule now actively applies. |
| VIII | PASS | SC-003 measured with the Feature 002 load script pointed at PostgreSQL. |
| IX | PASS | Same API; `postgresql://` URLs as Django/SQLAlchemy users expect. |
| X | PASS | Matches reordered roadmap Feature 003. |

## Project Structure

```text
crates/database/src/lib.rs         + Value::Decimal, Batch{returning}, DbError::MissingTable
crates/orm/src/compile.rs          + PostgresDialect, subquery alias
crates/postgres/src/{lib,config,tls,worker,params,cells,numeric,pgtime,errors}.rs
crates/postgres/tests/codec.rs     numeric/date/time codecs
crates/sqlite/src/worker.rs        returning batches, Decimal binding
crates/python-bridge/src/{db,orm,orm_values}.rs  scheme dispatch, Decimal values, returning bulk insert
tests/python/orm/conftest.py       backend-parametrized `database`, temporary PostgreSQL cluster
tests/python/orm/test_postgres.py  PostgreSQL-specific behavior
```

## Complexity Tracking

None.
