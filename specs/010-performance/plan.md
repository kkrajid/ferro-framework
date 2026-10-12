# Implementation Plan: Performance Suite & Multi-Process Serving

**Branch**: `010-performance` | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

## Summary

`ferro run --processes N`: a Python supervisor binds the port once and starts N worker
processes that adopt the shared listening socket through one new native entry point
(`Server.start(listener)` → `ferro_http::start_on`). A benchmark suite (`run_010.py`) measures
every scenario against the Rust-only baseline in one session, and a profiling mode decides
whether any boundary optimization is justified ([research.md](research.md)).

## Technical Context

**Language/Version**: Rust 1.99; Python ≥ 3.12 ·
**Dependencies**: none new · **Testing**: CLI subprocess tests
(`tests/integration/test_processes.py`), settings unit tests, the benchmark suite.

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| I Rust core, Python surface | PASS | Socket adoption and serving stay in Rust; the supervisor is process management only, never on the request path. |
| II Hidden engine | PASS | `start_on` takes a std `TcpListener`; no Actix type crosses the bridge. |
| III–IV ORM/SQL | PASS | Untouched; each worker has its own pool. |
| V Secure defaults | PASS | Same settings and policies in every worker; the socket is handed only to the processes the supervisor starts. |
| VI Safety | PASS | One new `unsafe` (raw socket/descriptor adoption) in the bridge crate with a `// SAFETY:` comment. |
| VII Test-first | PASS | `test_processes.py` written and observed red first. |
| VIII Measured performance | PASS | Claims come from `run_010.py`; optimizations only with a profile. |
| IX Familiar | PASS | `--processes` mirrors gunicorn/uvicorn `--workers`. |
| X Roadmap | PASS | Roadmap 010; worker processes deferred from Feature 001. |

## Project Structure

```text
crates/http/src/server.rs             start_on(settings, listener, ...)
crates/python-bridge/src/server.rs    Server.start(listener=None), adopt_listener (unsafe)
python/ferro/supervisor.py            supervise, adopt_listener, watch_supervisor
python/ferro/cli.py                   --processes, hidden _worker command
python/ferro/config.py                processes setting
python/ferro/server.py                ServerRunner/serve(listener=...)
tests/integration/test_processes.py   US1
tests/benchmarks/run_010.py           US2/US3
```
