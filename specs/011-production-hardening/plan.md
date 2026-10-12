# Implementation Plan: Production Hardening

**Branch**: `011-production-hardening` | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/011-production-hardening/spec.md`

## Summary

The engine answers liveness and readiness probes itself (readiness asks the bridge for a cached,
single-flight `SELECT 1`), fails readiness during a configurable drain before a graceful stop,
counts in-flight work and logs what shutdown finished or cut off, bounds request bodies by the
request timeout (408), and serves opt-in Prometheus-format metrics to allowed addresses.
Database connectivity failures become a distinct `DatabaseUnavailableError` answered with 503,
and a full database queue fails fast instead of blocking the Python thread. Request ids reach
Python log records through a context variable set by the bridge; logs can be JSON lines. A
permanent stress/failure suite proves the behaviour ([research.md](research.md)).

## Technical Context

**Language/Version**: Rust 1.99 (edition 2024); Python ≥ 3.12 (dev on CPython 3.13)
**Primary Dependencies**: none new (tokio `watch`, `serde_json` and `tracing-subscriber` are
already in the workspace)
**Storage**: SQLite and PostgreSQL through the existing backends (`queue_size` URL parameter)
**Testing**: `cargo test` (engine probes/metrics/body deadline, backend classification),
`pytest` (settings, logging, error mapping, routing collisions), `tests/stress/` (real servers,
both databases), `tests/benchmarks/run_011.py` + `ab_builds.py` (measurements)
**Target Platform**: Windows 11 (development), Linux/macOS (CI)
**Project Type**: framework (Rust engine + Python package)
**Performance Goals**: metrics on costs ≤ 5% of hello-world throughput; probes and request-id
propagation cost no measurable regression (≤ 5% investigated, > 10% blocks)
**Constraints**: no new crates; probes and metrics never run Python; database work never blocks
the Python thread; label cardinality bounded by the route table
**Scale/Scope**: 15 functional requirements, 5 user stories, ~10 stress scenarios

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|---|---|---|
| I Thin Python, Rust core | PASS | Probes, readiness check, metrics, body deadline, shutdown accounting, JSON formatting, error classification and the 503 mapping are Rust. Python adds: the drain wait in `serve()` (process control next to the existing signal handling), the logging glue (Python's `logging` API is the developer surface), settings validation. |
| II Engine encapsulation | PASS | `Dispatcher` gains two engine-neutral methods; no Actix/Tokio/driver types in Python or messages; probe bodies carry no engine names. |
| III Framework-owned ORM | PASS | Backends only gain an error variant and a queue parameter. |
| IV Safe SQL | PASS | The only new statement is the static `SELECT 1`; no values. |
| V Secure by default | PASS | Metrics off by default and loopback-only when on; probes reveal only status and failing check names; the new body deadline closes a slow-upload hole; 503 bodies contain no database text. |
| VI Coarse boundary, non-blocking | PASS | Database queue fails fast instead of blocking the Python thread (removes a latent deadlock); readiness check is async on engine threads; request-id propagation adds no crossing (set/reset inside the existing drain). Queues stay bounded. |
| VII Test-first + backend parity | PASS | Each phase's tests are written and observed failing first; database scenarios run on SQLite and PostgreSQL; the 500→503 change starts from failing regression tests. |
| VIII Measured performance | PASS | Metrics overhead and hot-path changes measured (R10). |
| IX Django-familiar | PASS | `/healthz`, `/readyz`, Prometheus format, `logging` with `%(request_id)s`, settings via existing precedence. |
| X Scope discipline | PASS | Roadmap 011 / vision Phase 10 and §19; OpenTelemetry, separate admin port and custom checks deferred. |

Re-check after Phase 1 design: unchanged (all PASS); no Complexity Tracking entries.

## Project Structure

### Documentation (this feature)

```text
specs/011-production-hardening/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-probes.md
│   ├── metrics.md
│   ├── settings.md
│   ├── python-api.md
│   └── logging.md
├── checklists/requirements.md
└── tasks.md            # /speckit-tasks
```

### Source Code (repository root)

```text
crates/core/src/dispatch.rs          Dispatcher::readiness / write_metrics (defaults)
crates/core/src/settings.rs          probe/metrics settings + validation
crates/database/src/lib.rs           DbError::Unavailable
crates/sqlite/src/{lib,errors}.rs    try_send + queue_size, busy/locked → Unavailable, URL params
crates/postgres/src/{lib,errors,worker,config}.rs  try_send + queue_size, connection classes → Unavailable
crates/http/src/service.rs           probes, metrics endpoint, body deadline, in-flight guard
crates/http/src/metrics.rs           counters/histograms + text exposition (new)
crates/http/src/server.rs            draining/stopping flags, begin_drain, shutdown summary log
crates/python-bridge/src/bridge.rs   request-id context around task creation
crates/python-bridge/src/dispatch.rs readiness check (cached SELECT 1), write_metrics
crates/python-bridge/src/db.rs       query timing/outcome for metrics
crates/python-bridge/src/errors.rs   DatabaseUnavailableError → 503 + warning log
crates/python-bridge/src/orm_values.rs  Unavailable/Closed → DatabaseUnavailableError
crates/python-bridge/src/logging.rs  JSON FormatEvent, request_id field, format parameter
crates/python-bridge/src/server.rs   new settings keys, begin_drain
python/ferro/config.py               new settings
python/ferro/server.py               drain, collision check, begin_drain
python/ferro/supervisor.py           drain-aware kill deadline
python/ferro/log.py                  record factory, root attachment, request_id forwarding
python/ferro/routing.py              copied context for sync handlers
python/ferro/models/exceptions.py    DatabaseUnavailableError (+ ferro.db re-export)
python/ferro/cli.py                  --log-format, --shutdown-drain
tests/support/{postgres,process}.py  shared helpers (moved)
tests/stress/                        stress/failure/shutdown suite (new)
tests/benchmarks/run_011.py          metrics overhead
```

**Structure Decision**: existing workspace layout; one new engine module (`metrics.rs`), one new
test package (`tests/stress/`) and a shared test-support package.

## Complexity Tracking

No constitution violations to justify.
