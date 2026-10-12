# Research: Runtime Core & Python Handlers

**Feature**: [spec.md](spec.md) | **Date**: 2026-10-10

Toolchain verified on the development machine: Rust 1.99.0 (stable, MSVC), MSVC 14.50, `uv`
0.10.0, CPython 3.12 / 3.13 / 3.14, maturin 1.15.0. Crate versions taken from crates.io on
2026-10-10.

## R1. HTTP engine and its encapsulation

- **Decision**: Actix Web 4.15 (`actix-web`, `actix-rt`) used only inside `crates/http`. Ferro
  does its own routing; Actix sees one catch-all `default_service`. The crate exposes a
  Ferro-owned `Dispatcher` trait plus `FerroRequest`/`FerroResponse` types from `crates/core`.
- **Rationale**: Constitution II requires the engine to be swappable. Using Actix's router would
  leak Actix path syntax and matching rules into Ferro's behavior; our own router keeps 308
  trailing-slash redirects, static-over-param precedence and 405/HEAD logic engine-independent and
  unit-testable without a server.
- **Alternatives considered**: Hyper directly (more code for keep-alive, limits, worker model —
  Actix already provides them); Axum (fine engine, but the product plan selected Actix and its
  per-worker single-threaded runtime suits non-`Send` Python handoff); Actix routing (rejected:
  leaks engine semantics).

## R2. Python ↔ Rust async bridge

- **Decision**: Ferro-owned bridge in `crates/python-bridge` built on `asyncio.run_coroutine_threadsafe`:
  1. Actix worker attaches to the interpreter, builds the `Request`, calls the route's endpoint
     (a Python coroutine function) to get a coroutine.
  2. `asyncio.run_coroutine_threadsafe(coro, loop)` schedules it on Ferro's Python execution
     thread and returns a thread-safe `concurrent.futures.Future`.
  3. A Rust `#[pyclass] Completion` holding a `tokio::sync::oneshot::Sender` is attached with
     `future.add_done_callback(completion)`; the worker awaits the receiver.
  4. A drop guard owns the Python future: if the Rust side times out or the client disconnects
     (Actix drops the service future), the guard calls `future.cancel()`, which cancels the
     asyncio task.
- **Rationale**: Inspection of `pyo3-async-runtimes` 0.29.0 `into_future_with_locals` shows it
  never cancels the Python task when the Rust future is dropped. FR-019 (timeout → 503 without
  affecting others) and the client-disconnect edge case require cancellation, so the handler must
  not keep running. The stdlib primitive gives cancellation, thread safety and exception
  propagation with ~150 lines of Rust and no extra dependency.
- **Alternatives considered**: `pyo3-async-runtimes` (rejected: no cancel-on-drop; would need a
  fork); driving `coro.send(None)` directly on Actix workers (fast for handlers that never await,
  but a suspended coroutine cannot be handed to asyncio safely — deferred to Feature 010 as an
  "eager execution" optimization). Constitution technology constraint amended to v1.1.0
  accordingly.

## R3. Concurrency model (clarified)

- **Decision**: One process. Actix runs `workers` I/O threads (default = CPU cores). One Python
  thread (`ferro-python`) runs the asyncio event loop for all async handlers. Sync handlers run via
  `loop.run_in_executor` on a bounded `ThreadPoolExecutor` (default 40 threads). Actix workers hold
  the interpreter only for short sections: building the request object, scheduling the coroutine,
  converting the result.
- **Rationale**: Matches the clarification; identical behavior on Windows, Linux and macOS.
  Python code runs on one core per process; Feature 010 adds multi-process serving.
- **Alternatives considered**: multi-process now (rejected in clarification: platform-specific
  socket sharing and supervision); a Python thread per Actix worker (all would contend for the
  same interpreter lock with more context switches and no throughput gain).

## R4. Signals and lifecycle

- **Decision**: Actix signal handling is disabled. The Python main thread starts the server
  (Rust spawns an `actix-rt` system thread and returns immediately with the bound address), then
  waits in `server.wait(timeout=0.25)` slices with the interpreter released. Python receives
  SIGINT (KeyboardInterrupt), SIGTERM and, on Windows, SIGBREAK; the handler calls
  `server.stop(graceful=True)`, which triggers Actix graceful shutdown bounded by
  `shutdown_timeout`. Then the asyncio loop is stopped and its thread joined.
- **Rationale**: Python only runs signal handlers on the main thread; waiting in short slices keeps
  Ctrl+C responsive on every platform and keeps one owner for shutdown. Embedding Ferro in tests is
  possible because nothing installs process-global handlers except the CLI path.
- **Alternatives considered**: Actix `ctrl_c` handling (conflicts with Python's SIGINT handler and
  leaves the asyncio thread unmanaged).

## R5. Socket binding

- **Decision**: Rust binds with `socket2` (`SO_REUSEADDR` on Unix only, backlog 1024), then hands
  the `std::net::TcpListener` to Actix via `HttpServer::listen`. Port `0` is allowed and the bound
  address is returned to Python.
- **Rationale**: Ephemeral ports make tests parallel-safe; `SO_REUSEADDR` lets the dev reloader
  rebind immediately on Unix. On Windows `SO_REUSEADDR` allows port hijacking, so it is not set
  there (Constitution V).
- **Alternatives considered**: `HttpServer::bind` (no control over options; no port-0 reporting).

## R6. Routing algorithm

- **Decision**: Segment trie in `crates/router`. Each node has a map of static children and at
  most one parameter child; lookup tries static first, then parameter, with backtracking so
  `/users/me/x` still reaches `/users/{id}/x` if `/users/me` has no `x` child. Per terminal node a
  method table (`GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS`). HEAD falls back to GET. If no
  terminal matches, the router retries with the trailing slash toggled and returns a redirect
  outcome. Percent-decoding happens per segment after splitting, so `%2F` never splits.
- **Rationale**: O(segments) lookup, deterministic precedence independent of registration order,
  no regex engine, fully unit-testable.
- **Alternatives considered**: `matchit` crate (radix tree, fast, but its conflict rules reject
  some static/param overlaps we must allow and adds a dependency for ~300 lines of code); regex
  list (order-dependent, slower).

## R7. JSON encoding and decoding

- **Decision**: Rust `serde_json` 1.0 with `arbitrary_precision`. Decoding: bytes → `serde_json::Value`
  → Python objects (integers outside i64/u64 become exact Python `int` via their textual form).
  Encoding: a hand-written writer walks Python objects (`dict`, `list`, `tuple`, `str`, `bool`,
  `int`, `float`, `None`, plus `datetime`/`date`/`time` → `isoformat()`, `Decimal`/`UUID` →
  `str()`), uses `serde_json` string escaping, preserves non-ASCII text, rejects NaN/Infinity and
  depth > 256, and converts `int`/`float`/`bool`/`None` dict keys to strings like Python's `json`.
  Type objects for the special types are fetched once at server start (works under `abi3`).
- **Rationale**: Constitution technology constraint: JSON happens in Rust. A direct writer avoids
  building an intermediate Rust value tree.
- **Alternatives considered**: `pythonize`/`serde-pyobject` (extra dependencies, no control over
  Decimal/UUID/datetime policy); calling Python `json` (violates the constraint and is slower).

## R8. Packaging and Python version

- **Decision**: maturin 1.15, mixed layout (`python-source = "python"`, module `ferro._ferro`),
  PyO3 0.29 with `abi3-py312` → one wheel per platform for CPython ≥ 3.12. Console script
  `ferro = "ferro.cli:main"`. Development uses `uv` with a `.venv` on CPython 3.13 and
  `maturin develop --uv`.
- **Rationale**: abi3 keeps distribution simple and meets "no Rust toolchain needed with wheels"
  (Constitution IX). PyO3 0.29 uses `Python::attach`/`Python::detach` and `Bound::cast`.
- **Alternatives considered**: version-specific wheels (faster FFI paths, more CI matrix — revisit
  in Feature 010 with benchmark data).

## R9. Dev auto-reload

- **Decision**: `ferro dev` runs a Python supervisor process that starts the server as a child
  (`python -m ferro run --reload-child ...`), polls `*.py` mtimes under the project directory every
  0.5 s (ignoring `.venv`, `venv`, `.git`, `__pycache__`, `node_modules`, `target`, `build`,
  `dist`), and on change stops the child gracefully (SIGTERM, or CTRL_BREAK on Windows with a new
  process group), force-kills after 5 s, and starts a fresh child. A child that exits with an
  import/syntax error is restarted on the next change.
- **Rationale**: Process restart guarantees clean state; polling has no dependencies and meets the
  2-second target. This is dev tooling, not request-path infrastructure, so Python is acceptable
  under Constitution I.
- **Alternatives considered**: Rust `notify` watcher (9.x is still RC; 8.x works but adds a native
  surface for a dev-only feature — revisit if polling cost shows up on large projects);
  `importlib.reload` in-process (leaves stale state; unsafe with native server threads).

## R10. Errors and request ids

- **Decision**: Python exception classes (`HTTPException`, `ValidationError(HTTPException)`,
  `ConfigurationError`) live in `python/ferro/exceptions.py`. The bridge receives the exception
  from the completed future; it checks `isinstance(exc, HTTPException)` using the class handed over
  at server start and reads `status`, `message`, `headers`, `details`. Anything else → 500; in
  debug mode the bridge adds `type`, `message` and `traceback` (from `traceback.format_exception`).
  Request ids: incoming `x-request-id` reused if it matches `^[A-Za-z0-9._-]{1,128}$`, otherwise a
  UUIDv4 (simple form, 32 hex chars).
- **Rationale**: Keeps exception classes ordinary Python classes developers can subclass, while
  the envelope is built in Rust (single format for engine-level and handler-level errors).

## R11. Logging

- **Decision**: `tracing` + `tracing-subscriber` (fmt layer, stderr, `EnvFilter` from
  `log_level`). Access log target `ferro.access` at INFO; errors at ERROR with request id. A Python
  `logging.Handler` (`ferro.log.RustHandler`) forwards the `ferro` logger to Rust through
  `_ferro.log(level, target, message)`.
- **Rationale**: Constitution technology constraint (one pipeline). Access logging stays in Rust,
  off the Python thread.

## R12. Test tooling

- **Decision**: Rust: `cargo test` unit tests in each crate plus `crates/http/tests` integration
  tests with a Rust-only dispatcher over a raw `TcpStream` client. Python: `pytest` + `httpx` with a
  fixture that starts the server in-process on port 0 using a background thread for the main-thread
  wait. CLI tests start `ferro run`/`ferro dev` as subprocesses. Load tests for SC-002/003/004 use
  `oha` (installed with `cargo install oha`).
- **Rationale**: Covers each layer independently (Constitution VII) with minimal dependencies.

## R13. Bridge v2 — measured redesign (during implementation)

- **Measurement**: with the R2 design (each engine worker attaches to the interpreter twice per
  request and uses `asyncio.run_coroutine_threadsafe`), hello world reached ~4,200 req/s
  (p50 22 ms, 100 clients, 4 workers, release build, Windows 11, 8 logical CPUs, `oha` on the
  same machine). A Rust-only dispatcher through the same adapter
  (`crates/http/examples/baseline.rs`) reached ~24,800 req/s, so the Python path ran at 17% of
  the engine ceiling, short of SC-002 (10,000 req/s).
- **Decision**: engine workers never touch the interpreter on the request path. They push jobs
  into a Rust queue and wake the event loop (`call_soon_threadsafe(drain)`) only when no drain
  is already pending. The drain runs on the Python execution thread, builds requests, starts
  handlers with `asyncio.eager_task_factory` (handlers that never suspend finish inside the
  drain, with no task scheduling), converts outcomes to responses there, and replies through
  oneshot channels. Cancellation is a queued job (`Job::Cancel`) that cancels the task on the
  loop thread, preserving the R2 guarantee.
- **Rationale**: removes per-request interpreter-lock handoffs between worker threads and the
  Python thread, removes `concurrent.futures.Future` and `threading.Condition` allocations, and
  batches wake-ups under load. All Python work stays on one thread, which matches R3.
- **Alternatives considered**: lowering `sys.setswitchinterval` (reduces lock convoy latency but
  keeps the handoffs); a dedicated pump thread (still two threads contending for the
  interpreter); running coroutines on workers (breaks any handler that awaits, see R2).
