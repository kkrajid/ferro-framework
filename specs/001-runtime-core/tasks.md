---
description: "Task list for Feature 001 — Runtime Core & Python Handlers"
---

# Tasks: Runtime Core & Python Handlers

**Input**: Design documents from `/specs/001-runtime-core/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: Required. Constitution VII (test-first, NON-NEGOTIABLE): every test task below is
written and observed failing before the implementation tasks of the same phase.

**Organization**: Tasks are grouped by user story so each story can be implemented and tested
independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story the task belongs to (US1–US6)

## Path Conventions

Cargo workspace in `crates/`, Python package in `python/ferro/`, tests in `tests/` (Python) and
inside each crate (Rust), examples in `examples/` — see [plan.md](plan.md#project-structure).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Repository skeleton, build system and tooling

- [X] T001 Create Cargo workspace `Cargo.toml` (members `crates/*`, edition 2024, shared `[workspace.dependencies]` with versions from plan.md, `[workspace.lints]` denying `unsafe_code` except in python-bridge) and empty crates `crates/core` (`ferro-core`), `crates/router` (`ferro-router`), `crates/http` (`ferro-http`), `crates/python-bridge` (`ferro-python`, `crate-type = ["cdylib", "rlib"]`, lib name `_ferro`)
- [X] T002 Create `pyproject.toml`: maturin backend, `module-name = "ferro._ferro"`, `python-source = "python"`, `manifest-path = "crates/python-bridge/Cargo.toml"`, `requires-python = ">=3.12"`, `[project.scripts] ferro = "ferro.cli:main"`, dev dependency group (pytest, httpx, ruff, maturin), ruff and pytest config
- [X] T003 [P] Create `.gitignore` (target/, .venv/, __pycache__/, *.pyd, *.so, dist/, .pytest_cache/, .ruff_cache/, .claude/settings.local.json), `rustfmt.toml`, `.editorconfig`
- [X] T004 [P] Create Python package skeleton `python/ferro/__init__.py`, `python/ferro/py.typed`, `python/ferro/__main__.py`, `python/ferro/_ferro.pyi` (stub surface from contracts/rust-python-boundary.md)
- [X] T005 Create dev environment (`uv venv --python 3.13 .venv`, install dev group, `maturin develop --uv`) and smoke-check `python -c "import ferro._ferro"` — native module exposes `__version__`
- [X] T006 [P] Create CI workflow `.github/workflows/ci.yml` running fmt, clippy `-D warnings`, `cargo test`, ruff, maturin develop, pytest on ubuntu/windows/macos with Python 3.12 and 3.13

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Engine-independent core, router, Actix adapter, bridge plumbing and Python server
lifecycle that every story needs

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Tests (write first, must fail)

- [X] T007 [P] Tests for settings validation, request-id acceptance/generation, error envelope JSON and security header set in `crates/core/tests/core.rs`
- [X] T008 [P] Unit tests for path parsing (valid, unbalanced braces, empty/duplicate/invalid param names), static-over-param precedence with backtracking, HEAD→GET fallback, 405 allow list, 308 trailing-slash outcome, percent-decoding without splitting on `%2F`, duplicate detection in `crates/router/tests/router.rs`
- [X] T009 [P] Integration tests in `crates/http/tests/server.rs` using a Rust-only `Dispatcher` and raw `TcpStream` client: bind on port 0, 200 response, 404/405/308 envelopes from router outcomes, 413 for oversized body (handler not called), `x-request-id` generated/propagated, security headers present and no `server` header, 503 on dispatcher exceeding timeout, graceful stop waits for in-flight request
- [X] T010 [P] Tests for the Rust JSON codec (round trip of nested values, big ints exact, NaN rejected, depth limit, non-str keys, non-ASCII preserved) in `tests/python/test_json_codec.py` through `ferro._ferro.json_dumps/json_loads` (the native module can only load inside Python, so a `cargo test` harness was not used)
- [X] T011 [P] Python tests for configuration precedence and validation in `tests/python/test_config.py` (CLI > env > App > default, source tracking, invalid values raise `ConfigurationError` naming setting and source)

### Implementation

- [X] T012 [P] Implement `ferro-core`: `Settings`, `FerroRequest`, `FerroResponse`, `Body`, `Dispatcher` trait (`LocalBoxFuture` return), `ErrorEnvelope` rendering, `request_id`, `security_headers` in `crates/core/src/`
- [X] T013 [P] Implement `ferro-router`: `PathPattern` parser, segment trie, `Router::builder().add(methods, path, id)`, `Router::lookup(method, path) -> Match` in `crates/router/src/lib.rs`
- [X] T014 Implement `ferro-http`: socket2 bind (no `SO_REUSEADDR` on Windows), `HttpServer` with workers/max connections/header timeout/shutdown timeout/`disable_signals`, single default service doing body limit, routing, dispatch with request timeout, envelope responses, request id, security headers, access log; `ServerHandle { addr, stop(graceful), wait(timeout) }` in `crates/http/src/{lib,server,service}.rs`
- [X] T015 Implement JSON codec (`encode(py, obj) -> Vec<u8>`, `decode(py, bytes) -> PyObject`) and `json_dumps`/`json_loads` module functions in `crates/python-bridge/src/json.rs`
- [X] T016 Implement logging bridge (`init_logging`, `log`) in `crates/python-bridge/src/logging.rs` and Python handler in `python/ferro/log.py`
- [X] T017 Implement asyncio bridge in `crates/python-bridge/src/bridge.rs`: `Completion` pyclass (oneshot sender), `schedule(endpoint, request) -> PendingCall` using `asyncio.run_coroutine_threadsafe`, drop guard that cancels the concurrent future
- [X] T018 Implement `Server` pyclass (`__init__` builds router from route tuples, `start`, `wait` with interpreter detached, `stop`) and the `PyDispatcher` implementing `Dispatcher` in `crates/python-bridge/src/{server,dispatch}.rs`; module definition in `crates/python-bridge/src/lib.rs`
- [X] T019 Implement `python/ferro/exceptions.py` (`HTTPException`, `ValidationError`, `ConfigurationError`) and `python/ferro/response.py` (`Response`, `JSONResponse`)
- [X] T020 Implement `python/ferro/config.py` (`Settings`, `resolve(cli, env, app)`, validation, source tracking)
- [X] T021 Implement `python/ferro/server.py`: `serve(app, settings, *, install_signals)` creating the asyncio loop thread, sync executor, `Server` start/wait/stop, SIGINT/SIGTERM/SIGBREAK handling, startup banner; plus `tests/conftest.py` fixture `running_app` that serves an `App` in-process on port 0 and yields an `httpx.Client`

**Checkpoint**: Foundation ready — `cargo test --workspace` and `pytest tests/python/test_config.py` pass

---

## Phase 3: User Story 1 - Hello World in minutes (Priority: P1) 🎯 MVP

**Goal**: `@app.get("/")` returning a dict is served as JSON by `ferro dev`

**Independent Test**: quickstart Scenario 1

### Tests for User Story 1 (write first, must fail)

- [X] T022 [P] [US1] `tests/python/test_hello.py`: GET / → 200 JSON body and content type; `/missing` → 404 envelope with request_id; POST / → 405 with `allow: GET, HEAD`; HEAD / → 200 empty body; security headers present
- [X] T023 [P] [US1] `tests/integration/test_cli_hello.py`: `ferro dev --port 0`-style run on a free port in `examples/hello_world` as subprocess; banner printed; GET / works; interrupt → exit code 0

### Implementation for User Story 1

- [X] T024 [US1] Implement `App` with `get` decorator, route records and `routes` property in `python/ferro/app.py` (path validated via `_ferro.parse_path`)
- [X] T025 [US1] Implement minimal endpoint wrapper (no-arg and `request`-arg handlers) in `python/ferro/routing.py`
- [X] T026 [US1] Implement result conversion for dict/list/tuple → JSON response in `crates/python-bridge/src/convert.rs`
- [X] T027 [US1] Implement `ferro dev` / `ferro run` target loading (`module:attr`, cwd on `sys.path`) in `python/ferro/cli.py`
- [X] T028 [P] [US1] Create `examples/hello_world/app.py` (home route; later stories add routes) and export public API in `python/ferro/__init__.py`

**Checkpoint**: US1 fully functional — first milestone of the product vision (§25) reached

---

## Phase 4: User Story 2 - Path and query parameters (Priority: P1)

**Goal**: Path/query values injected by name with type conversion; 422 on bad input; 308 slash redirects

**Independent Test**: quickstart Scenario 2

### Tests for User Story 2 (write first, must fail)

- [X] T029 [P] [US2] `tests/python/test_params.py`: int conversion, query default, missing required query → 422 details, bad int → 422 naming `id`, bool parsing, Optional, `request.query.getlist`, static `/users/me` beats `/users/{id}` in both registration orders, 308 redirect with query preserved, percent-decoded path params, unsupported annotation / `*args` rejected at decoration with `ConfigurationError`

### Implementation for User Story 2

- [X] T030 [US2] Implement signature analysis (`ParamSpec`), converters and `ValidationError` aggregation in `python/ferro/routing.py`
- [X] T031 [US2] Implement `Request.query` (`QueryParams`), `path_params`, `headers` (`Headers`), `client`, `request_id`, `path`, `raw_path`, `query_string` in `crates/python-bridge/src/request.rs`
- [X] T032 [US2] Add `/products/{id}` and `/users/me` + `/users/{id}` routes to `examples/hello_world/app.py`

**Checkpoint**: US1 + US2 work independently

---

## Phase 5: User Story 3 - JSON APIs with every HTTP method (Priority: P2)

**Goal**: All methods, JSON request bodies, explicit status/headers, all return-value kinds

**Independent Test**: quickstart Scenario 3

### Tests for User Story 3 (write first, must fail)

- [X] T033 [P] [US3] `tests/python/test_bodies.py`: post/put/patch/delete/route decorators; `await request.json()`; malformed JSON → 400 and handler stops; non-JSON content type → 400; `Response(status=201, headers=...)`; str/bytes/None/JSONResponse conversions; 2 MiB body → 413 without handler call; header names/values validated (CR/LF rejected → 500 in log); datetime/Decimal/UUID JSON encoding

### Implementation for User Story 3

- [X] T034 [US3] Add `post`, `put`, `patch`, `delete`, `route` decorators in `python/ferro/app.py`
- [X] T035 [US3] Implement awaitable `body()` / `json()` on `Request` (ready-awaitable pyclass) in `crates/python-bridge/src/request.rs`
- [X] T036 [US3] Extend `crates/python-bridge/src/convert.rs` for str, bytes/bytearray/memoryview, None → 204, `Response`/`JSONResponse` (status, header validation, media type inference)
- [X] T037 [US3] Add create-product route to `examples/hello_world/app.py`

**Checkpoint**: US1–US3 work independently

---

## Phase 6: User Story 4 - Safe, helpful error handling (Priority: P2)

**Goal**: Consistent envelopes; production 500s leak nothing; debug 500s are detailed; timeouts → 503

**Independent Test**: quickstart Scenario 4

### Tests for User Story 4 (write first, must fail)

- [X] T038 [P] [US4] `tests/python/test_errors.py`: `HTTPException(404, ...)` envelope and custom headers; production 500 body has exactly status/message/request_id and no traceback/path/engine name; debug 500 includes type/exception/traceback; unserializable return (set) → 500; timeout (request_timeout=0.2, handler sleeps 2 s) → 503 and handler observed `CancelledError`; other requests unaffected during timeout; log contains traceback with same request id

### Implementation for User Story 4

- [X] T039 [US4] Implement exception mapping (HTTPException / ValidationError / CancelledError / other) and debug detail in `crates/python-bridge/src/errors.rs`
- [X] T040 [US4] Log handler failures with traceback and request id at ERROR in `crates/python-bridge/src/errors.rs`; ensure `crates/http` 503 path cancels via drop guard
- [X] T041 [US4] Add `/orders/{id}` and `/boom` routes to `examples/hello_world/app.py`

**Checkpoint**: US1–US4 work independently

---

## Phase 7: User Story 5 - Development and production commands (Priority: P2)

**Goal**: `ferro dev` (reload, debug), `ferro run` (production), `ferro routes`, config precedence, graceful shutdown, access log

**Independent Test**: quickstart Scenario 5

### Tests for User Story 5 (write first, must fail)

- [X] T042 [P] [US5] `tests/integration/test_cli.py`: `run` hides debug detail and `dev` shows it; `myapp.main:application` target; bad module / bad attribute → exit 2 with clear message; `FERRO_PORT` vs `--port` precedence; `ferro routes` table without server; port in use → exit 3; access log line format; graceful shutdown with in-flight requests completes them (SC-007); `--version`
- [X] T043 [P] [US5] `tests/integration/test_reload.py`: edit file → new response within 2 s; syntax error → error shown, server resumes after fix

### Implementation for User Story 5

- [X] T044 [US5] Complete `python/ferro/cli.py`: argparse subcommands, all server options, exit codes 0/1/2/3, error formatting
- [X] T045 [US5] Implement `ferro routes` table output in `python/ferro/cli.py`
- [X] T046 [US5] Implement dev supervisor in `python/ferro/reloader.py` (mtime polling, child process group, graceful stop then kill, restart on change)
- [X] T047 [US5] Implement `App.run()` in `python/ferro/app.py` delegating to `ferro.server.serve`

**Checkpoint**: US1–US5 work independently

---

## Phase 8: User Story 6 - Synchronous handlers without stalling the server (Priority: P3)

**Goal**: `def` handlers run on the bounded executor without delaying async routes

**Independent Test**: quickstart Scenario 6 (sync part)

### Tests for User Story 6 (write first, must fail)

- [X] T048 [P] [US6] `tests/python/test_sync_handlers.py`: sync handler returns all response kinds; 10 concurrent 1-second sync calls while fast route p99 stays < 50 ms; sync handler with params and `request`

### Implementation for User Story 6

- [X] T049 [US6] Run sync handlers through `loop.run_in_executor(app executor, ...)` in `python/ferro/routing.py`; executor sized by `sync_threads` in `python/ferro/server.py`
- [X] T050 [US6] Add `/sync-sleep` route to `examples/hello_world/app.py`

**Checkpoint**: All user stories independently functional

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T051 [P] FR-029 leak test `tests/python/test_no_engine_leak.py`: scan headers, bodies (debug and production) and captured logs for `actix`, `tokio`, `pyo3`, `hyper`
- [X] T052 [P] `README.md` with install, hello world, CLI and links to specs; `docs/getting-started.md`
- [X] T053 [P] Cross-platform load-test script `tests/benchmarks/run_001.py` (oha; replaces the planned .ps1/.sh pair) and results file `tests/benchmarks/results/001-runtime-core.md` (SC-002–SC-004)
- [X] T054 Run full gates (fmt, clippy, cargo test, ruff, pytest) and quickstart.md validation; record outcomes
- [X] T055 Update `docs/roadmap.md` status for Feature 001

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → user stories → **Polish (Phase 9)**
- T005 needs T001, T002, T004. Within Phase 2: T012 before T014/T018; T013 before T014/T018;
  T014–T017 before T018; T019–T020 before T021; T018 before T021.

### User Story Dependencies

- **US1 (P1)**: after Foundational. No story dependencies.
- **US2 (P1)**: after Foundational; extends `routing.py` and `request.rs` created/used by US1.
- **US3 (P2)**: after Foundational; extends `convert.rs` and `app.py`.
- **US4 (P2)**: after Foundational; error mapping independent of US2/US3.
- **US5 (P2)**: after US1 (needs CLI target loading).
- **US6 (P3)**: after US1 (needs endpoint wrapper).

### Within Each User Story

Tests first (failing) → Rust bridge pieces → Python API → example update → story checkpoint.

### Parallel Opportunities

- T003, T004, T006 in parallel; T007–T011 in parallel; T012 and T013 in parallel.
- Each story's test task is [P] with other stories' test tasks.
- US3 and US4 can be developed in parallel after US1; US5 and US6 in parallel after US1.

---

## Parallel Example: User Story 2

```bash
Task: "T029 [US2] tests/python/test_params.py"
Task: "T031 [US2] Request accessors in crates/python-bridge/src/request.rs"   # after T029 fails
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 Setup → Phase 2 Foundational → Phase 3 US1
2. **STOP and VALIDATE**: quickstart Scenario 1 — the product's first technical milestone

### Incremental Delivery

US1 → US2 (real endpoints) → US3 (write APIs) → US4 (safe errors) → US5 (production commands) →
US6 (sync handlers) → Polish. Each story ends at a checkpoint with its quickstart scenario passing.

---

## Notes

- Commit after each task or logical group (Conventional Commits).
- Stop at any checkpoint to validate a story independently.
- Engine names must never appear outside `crates/http` and `crates/python-bridge` internals.

## Implementation Notes (recorded during /speckit-implement)

- **Test-first observance.** Red was observed before implementation for: core and router
  (T007/T008 → T012/T013), the HTTP adapter (T009 → T014), config and JSON codec (T010/T011),
  US1 (T022/T023), US2 (T029), US5 (T042/T043) and US6 (T048). US3 (T033) and US4 (T038)
  passed on first run because the bridge pieces they exercise (T035/T036 conversions and
  `body()`/`json()`, T039/T040 error mapping and timeout cancellation) were written together
  with the foundational bridge (T015–T018). Behavior is covered, but those two stories did not
  get a red phase. Future features keep bridge work inside each story's phase to avoid this.
- **T010** runs through pytest because the native module only loads inside Python.
- **T009** also covers FR-028 (header-read timeout, connection cap), closing the MEDIUM gap
  reported by `/speckit-analyze`.
- **Contract change:** `ferro._ferro.Server` no longer takes a `types` dict; it imports the
  Python classes it needs lazily (contracts/rust-python-boundary.md updated).
- **Performance (T053, research R13).** First measurement 4,225 req/s (17% of the Rust-only
  baseline of ~24,800 req/s) failed SC-002. The bridge was redesigned so engine workers never
  touch the interpreter; result: 18,886 req/s (76% of baseline), SC-002, SC-003 and SC-004 all
  PASS. Results: `tests/benchmarks/results/001-runtime-core.md`.
- **Quickstart validation (T054)** found one defect: `ferro dev nonexistent:app` with
  auto-reload waited forever instead of exiting with code 2. Fixed (`check_target` before the
  reloader starts), with regression test `test_missing_module_exits_2_even_with_reload`.
- **Gates at completion:** `cargo fmt --check`, `cargo clippy -D warnings`, `cargo test`
  (47 tests), `ruff check`, `ruff format --check`, `pytest` (128 tests): all pass on Windows 11 /
  CPython 3.13. Linux/macOS and CPython 3.12 are covered by CI, which has not run yet.
