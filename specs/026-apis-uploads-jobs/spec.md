# Feature Specification: APIs, Dependencies, Uploads and Jobs

**Feature Branch**: `026-apis-uploads-jobs`

**Created**: 2026-10-21

**Status**: Implemented

**Input**: A roadmap from the user: P1 OpenAPI with authentication schemes and endpoint
metadata; P1 dependency injection with nested dependencies, request-scoped services, async
cleanup, database session injection and test overrides; P1 independent, reproducible
benchmarks in CI; uploads buffered in memory; background tasks lost on a crash.

## Clarifications

### Session 2026-10-21

- Q: Which items? → A: All four (OpenAPI auth/metadata, DI overrides plus database session,
  CI benchmarks, upload streaming plus durable jobs), then release 0.6.0rc1.
- Q: Overrides? → A: `app.dependency_overrides` (FastAPI spelling), read at request time, so
  tests change it after routes are registered. Replacements are analyzed once and cached;
  results are cached per request under the original dependency.
- Q: Database session? → A: The ORM has no session object; `request_transaction` is a generator
  dependency wrapping the request in `transaction()`.
- Q: Security schemes? → A: Callable objects used with `Depends`, FastAPI-like; they read
  credentials and raise 401; the OpenAPI builder finds them through nested dependencies.
  `login_required` and friends mark handlers with the session scheme.
- Q: Uploads? → A: The engine still reads the whole body before the handler (limits and
  multipart parsing stay in Rust), but past 1 MiB it writes to an unnamed temporary file off
  the event loop and memory-maps it (`ferro_shared::MappedFile`, the only `unsafe`). The
  multipart parser works unchanged on the map; uploads reach Python as zero-copy `BodySlice`
  views read in pieces.
- Q: Jobs? → A: A table in the application's database (transactional enqueue, no broker),
  claimed with `SELECT ... FOR UPDATE SKIP LOCKED` plus a conditional update; at-least-once
  with a renewed visibility timeout; retries with backoff; failed jobs kept.
- Q: Benchmarks? → A: A `workflow_dispatch` workflow on `ubuntu-latest`: release wheel,
  pinned competitor versions, uvicorn with uvloop and httptools, results with hardware and
  latency percentiles as job summary and artifact.

## Requirements *(mandatory)*

- **FR-001**: Dependency overrides, request transactions.
- **FR-002**: Security scheme dependencies and OpenAPI security.
- **FR-003**: Route metadata and `status_code`.
- **FR-004**: Large bodies on disk; uploads read in pieces.
- **FR-005**: Durable jobs and a worker command.
- **FR-006**: A reproducible benchmark workflow.

## Success Criteria *(mandatory)*

- **SC-001**: Tests cover each requirement on SQLite and PostgreSQL where a database is used
  (`tests/python/test_di_openapi_security.py`, `tests/python/orm/test_request_transaction.py`,
  `tests/python/test_upload_spooling.py`, `tests/python/orm/test_jobs.py`, Rust
  `spool::tests`).
- **SC-002**: The full suite, lint and Rust checks pass on every CI platform.
