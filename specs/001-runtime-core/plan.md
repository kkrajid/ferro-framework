# Implementation Plan: Runtime Core & Python Handlers

**Branch**: `001-runtime-core` | **Date**: 2026-10-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-runtime-core/spec.md`

## Summary

Deliver Ferro's first vertical slice: a Python developer declares an `App` with decorated async or
sync handlers and serves it with `ferro dev` / `ferro run`. Rust owns the server (Actix Web behind
a Ferro-owned adapter), routing (segment trie with static-over-param precedence, HEAD, 405 and 308
trailing-slash redirects), limits, timeouts, request ids, security headers, access logging, JSON
encode/decode and error envelopes. Python owns the developer API (decorators, signature-based
parameter injection, `Response`/`HTTPException` classes, configuration precedence, CLI and dev
reloader). A Ferro-owned asyncio bridge runs handlers on one Python execution thread and cancels
them on timeout or client disconnect (see [research.md](research.md) R2–R3).

## Technical Context

**Language/Version**: Rust 1.99 stable (edition 2024); Python ≥ 3.12 (dev on CPython 3.13)

**Primary Dependencies**: actix-web 4.15, actix-rt 2, tokio 1 (sync, time), pyo3 0.29
(`abi3-py312`), serde_json 1 (`arbitrary_precision`), socket2 0.6, tracing 0.1,
tracing-subscriber 0.3, uuid 1, percent-encoding 2, futures-util 0.3; build: maturin 1.15

**Storage**: N/A (database work starts in Feature 002)

**Testing**: `cargo test` (unit + `crates/http/tests` integration), `pytest` + `httpx`
(in-process server on port 0 and CLI subprocess tests), `oha` load runs for SC-002–SC-004

**Target Platform**: Windows, Linux, macOS (x86_64/arm64); Linux primary for production

**Project Type**: Library + CLI (Python framework with native extension, Cargo workspace)

**Performance Goals**: ≥ 10,000 req/s hello world, 100 concurrent clients, 4 cores (SC-002);
p99 < 50 ms for a fast route while a sync route blocks (SC-004)

**Constraints**: engine workers make no interpreter access on the request path in steady state
(bridge v2, research R13); all Python work for a request happens in one batch on the Python
execution thread; no blocking on Actix workers; 1 MiB default body limit; production 500s leak
nothing (SC-006)

**Scale/Scope**: 1,000 concurrent clients without errors (SC-003); ~4 Rust crates, ~10 Python
modules

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|---|---|---|
| I. Thin Python, Rust Core | PASS | HTTP, routing, limits, timeouts, JSON, error envelopes, logging, request ids in Rust. Python: decorators, parameter injection, config precedence, CLI, dev reloader (dev tooling, R9). |
| II. Engine Encapsulation | PASS | Actix only in `crates/http`; `crates/core` defines `FerroRequest`/`FerroResponse`/`Dispatcher`; FR-029 tested by grepping responses/logs for engine names. |
| III. Framework-Owned ORM | N/A | No ORM in this feature. |
| IV. Safe SQL by Construction | N/A | No SQL in this feature. |
| V. Secure by Default | PASS | Loopback default bind, 1 MiB body limit, header-read timeout, connection cap, request timeout, `nosniff` + referrer policy, no `server` header, no `SO_REUSEADDR` on Windows, production 500s scrubbed. |
| VI. Coarse Boundary, Non-Blocking Runtime | PASS | Workers hand requests to the Python thread through a Rust queue and receive finished responses (R13); body bytes copied once into a Python `bytes` only when the handler asks; sync handlers on bounded pool. The request queue is bounded in practice by `max_connections` (one in-flight request per connection). |
| VII. Test-First with Backend Parity | PASS | Tasks order tests before implementation per story; backend parity N/A (no DB). |
| VIII. Measured Performance | PASS | SC-002–SC-004 measured with `oha`; no performance claims in README beyond recorded numbers. |
| IX. Django-Familiar DX | PASS | `@app.get("/path/{id}")`, FastAPI-style parameter injection, `.pyi` stubs for `_ferro`, `examples/hello_world`. |
| X. Scope Discipline | PASS | Maps to roadmap Feature 001; TLS, HTTP/2, middleware API, multi-process explicitly deferred. |

Technology-constraint deviation: the constitution v1.0.0 named `pyo3-async-runtimes` for async
interop; research R2 shows it cannot cancel Python tasks. Resolved by amending the constitution to
v1.1.0 (Ferro-owned asyncio bridge) before implementation — not a violation carried into the plan.

**Post-design re-check (after Phase 1)**: PASS — contracts keep all engine types private; the
boundary contract caps crossings; no new violations.

## Project Structure

### Documentation (this feature)

```text
specs/001-runtime-core/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── python-api.md          # Public Python API
│   ├── cli.md                 # `ferro` command contract
│   ├── http-behavior.md       # Status codes, envelopes, headers
│   └── rust-python-boundary.md# Internal bridge contract (_ferro module)
├── checklists/requirements.md
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
Cargo.toml                      # workspace: crates/*
pyproject.toml                  # maturin build, project metadata, ruff/pytest config
crates/
├── core/                       # ferro-core: Settings, FerroRequest/FerroResponse, Dispatcher trait,
│   └── src/                    #   ErrorEnvelope, request-id, security headers (no engine deps)
├── router/                     # ferro-router: path pattern parser + segment trie + match outcomes
│   └── src/
├── http/                       # ferro-http: Actix adapter (only crate importing actix)
│   ├── src/                    #   server start/stop, limits, timeouts, access log, envelope rendering
│   └── tests/                  #   integration tests with a Rust-only dispatcher
└── python-bridge/              # ferro-python: PyO3 cdylib `ferro._ferro`
    └── src/                    #   Request pyclass, asyncio bridge, JSON codec, response conversion,
                                #   error mapping, Server pyclass, logging bridge
python/ferro/
├── __init__.py                 # public exports
├── _ferro.pyi                  # stubs for the native module
├── py.typed
├── app.py                      # App, route decorators, route table
├── routing.py                  # signature analysis, converters, endpoint wrappers (sync → executor)
├── response.py                 # Response, JSONResponse
├── exceptions.py               # HTTPException, ValidationError, ConfigurationError
├── config.py                   # Settings resolution CLI > env > App > default
├── server.py                   # serve(): event-loop thread, signals, Server lifecycle
├── reloader.py                 # dev supervisor (poll + restart child)
├── log.py                      # logging.Handler → Rust tracing
├── cli.py                      # `ferro dev|run|routes`
└── __main__.py
examples/hello_world/app.py
tests/
├── python/                     # pytest: API, params, responses, errors, config
├── integration/                # pytest: CLI subprocess, reload, shutdown, sync isolation
└── benchmarks/                 # oha scripts for SC-002–SC-004
```

**Structure Decision**: Cargo workspace with four crates matching the vision layout names
(`core`, `router`, `http`, `python-bridge`); the remaining vision crates are created by the
features that need them. Python sources in `python/ferro` (maturin mixed layout). Tests follow the
vision's `tests/` tree; Rust tests live with their crates.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Parameter injection and type conversion in Python (`routing.py`) | Reads Python signatures/annotations, which only exist in Python; work is per-parameter dict lookups | Doing it in Rust needs the same Python attribute access through FFI for each parameter — more crossings, not fewer. Revisit with benchmarks in Feature 010. |
| Dev reloader in Python | Dev-only process supervision, not on the request path | Rust watcher adds a native surface for no production benefit (research R9). |
