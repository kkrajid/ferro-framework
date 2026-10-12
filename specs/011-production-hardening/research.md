# Research: Production Hardening

**Feature**: [spec.md](spec.md) | **Date**: 2026-10-11

Starting point (survey of the code before planning): request ids are generated/propagated in the
engine and appear in response headers, error envelopes and access logs; graceful shutdown stops
accepting connections and lets in-flight requests finish within `shutdown_timeout`; the request
timeout answers 503 + `Retry-After: 1` and covers only the handler; body size, header timeout,
connection limit and rate limits exist. There is no probe, metric, drain, structured log or
request-id propagation into Python logging; an unhandled database connection error is a 500; a
full database queue blocks the Python thread.

## R1. Probes in the engine

- **Decision**: `ferro_http` answers `health_path` (default `/healthz`) and `ready_path` (default
  `/readyz`) in `handle()` before CORS preflight and routing, so no Python, middleware, rate
  limit or CSRF check runs. `GET`/`HEAD` only (others: 405 envelope with `Allow`). Bodies:
  `{"status": "ok"}`; `{"status": "ready"}`; `{"status": "not ready", "failing": [...]}` (503).
  `Cache-Control: no-store`. Probe requests are logged on `ferro.access` at debug level only. An
  empty path setting disables that probe.
- **Collisions** (FR-004): `ServerRunner` compares every registered route path (trailing slash
  ignored) with the probe and metrics paths before starting and raises `ConfigurationError`
  naming the handler and the setting. A parameter route such as `/{page}` is not a collision:
  probes are answered first and the address is reserved.
- **Alternatives**: Python routes registered by `App` (rejected: Python on the probe path, and a
  stuck interpreter would make liveness depend on it — liveness must stay engine-only); a
  separate port (rejected for probes: orchestrators expect the serving port; reconsidered for
  metrics in R8).

## R2. Readiness checks

- **Decision**: the engine's `State` holds a `draining` flag; readiness fails with `server` while
  it is set. For everything else the engine asks the dispatcher: a new
  `Dispatcher::readiness() -> LocalBoxFuture<Vec<&'static str>>` (default: no failing checks).
  The Python bridge implements it: without a database, nothing fails; with one, it runs
  `SELECT 1` through the backend (no Python) with a 750 ms limit and keeps the answer for 1
  second. A single check runs at a time (`tokio::sync::watch` + an "in progress" flag); probes
  arriving meanwhile wait for it (up to 0.85 s). Failing name: `database`.
- **Rationale**: SC-001 needs outage/recovery visible within 2 s: 1 s cache + 0.75 s check (a 1 s limit left no margin: on Windows a connection attempt to a stopped local server takes 1–2 s to fail). The
  cache bounds database load from probe floods (US1 scenario 6). `SELECT 1` is a static statement
  (no parameters; constitution IV).
- **Alternatives**: background polling task (rejected: load on the database even when nobody
  probes; more lifecycle code); application-defined checks (out of scope, clarification 2).

## R3. Drain and shutdown accounting

- **Decision**: new setting `shutdown_drain` (seconds, 0–3600, default 0; Python-side). On the
  first stop signal `serve()` calls `ServerRunner.begin_drain()` (native: sets `draining`), keeps
  serving for `shutdown_drain` seconds while readiness fails, then calls `stop(graceful=True)`
  as today; a second signal at any point stops non-gracefully (existing path). The supervisor
  forwards the signal once; each worker drains itself; its kill deadline grows by the drain.
- **Accounting** (FR-006): `handle()` holds a guard that increments `in_flight` on entry and on
  exit records "finished" or, if its future is dropped unfinished, "cut off". Counts only start
  when `RunningServer::stop` is called (flag `stopping`), so client disconnects during normal
  operation are not counted. When the server thread ends it logs on `ferro.server` (info):
  `stopped: N requests in flight at stop, F finished, C cut off after shutdown_timeout`.
- **Alternatives**: draining in Rust with a timer (rejected: the signal handling and second-signal
  semantics already live in `serve()`; the drain is process control, not request handling).

## R4. Request body time bound

- **Decision**: one deadline per request, `request_timeout` after the headers are parsed
  (`handle()` start). Reading the body uses `timeout_at(deadline)`: on expiry 408 envelope and
  the connection is closed. The handler uses the rest of the same deadline: on expiry the
  existing 503 + `Retry-After: 1` (Feature 001 contract unchanged).
- **Rationale**: closes the slow-body hole (connections held indefinitely by clients trickling a
  body) without a new setting. 408 tells the client the fault is its upload.
- **Alternatives**: separate `body_timeout` setting (rejected: YAGNI; one budget is easier to
  reason about); full `request_timeout` for each phase (rejected: a request could take twice the
  configured time).

## R5. Database unavailable → 503; fail fast when the queue is full

- **Decision**: new `DbError::Unavailable { message }`. Backends classify:
  - PostgreSQL: connection failures at connect, a lost connection, non-database communication
    errors, the session-lost error, and SQLSTATE classes `08` (connection exception), `53300`
    (too many connections), `57P01`–`57P03` (shutdown / cannot connect now), `55P03` (lock not
    available).
  - SQLite: `SQLITE_BUSY` / `SQLITE_LOCKED` (busy timeout expired).
  - Both: a full job queue (`try_send` instead of the blocking send), and `DbError::Closed`.
- Python gets `ferro.models.DatabaseUnavailableError(OperationalError)` (also re-exported from
  `ferro.db`), so existing `except OperationalError` keeps working. The bridge's error mapping
  turns an unhandled `DatabaseUnavailableError` into a 503 envelope with `Retry-After: 1` and logs
  one warning line on `ferro.handler` with the request id (no traceback; the message is the
  database's, never shown to the client).
- **Queue size**: still bounded (constitution VI); default 1024, now configurable with the URL
  parameter `queue_size` for both backends (SQLite URLs gain query parameters), so tests and
  operators can bound the backlog.
- **Why fail fast**: `submit` runs on the Python thread with the interpreter lock held. Blocking
  there stalls every request of the process; and since a backend thread must take the lock to
  wake the loop when no drain is pending, a full queue could deadlock the process (latent since
  Feature 002, made less likely by Feature 010's queue change but still possible).
- **Alternatives**: awaiting queue space asynchronously (rejected: unbounded waiting work in
  Python, and overload should shed load); mapping every `OperationalError` to 503 (rejected: a
  missing table or SQL error is a bug, not unavailability).

## R6. Request ids in application logs

- **Decision**: a Rust-created `ContextVar` `_ferro.request_id_context`. The bridge sets it
  around task creation in `Executor::start` (set → create the eager task, which copies the
  context → reset), so each handler task carries its request id; sync handlers get a copied
  context (`contextvars.copy_context().run` in `run_in_executor`) so code on the executor thread
  sees it too. `ferro.log` installs a chained `LogRecord` factory adding `record.request_id`
  (or `None`), and `RustHandler` passes it to the native log call, which emits it as the
  `request_id` field.
- **Application loggers**: `ferro.log.configure` attaches `RustHandler` to the root logger and
  sets its level only when the root logger has no handlers, or only Ferro's (the application has
  not configured logging); otherwise it leaves logging alone — the record attribute still lets
  the application's own formats use `%(request_id)s`.
- **Cost**: two `ContextVar` calls per request on the Python thread; measured before/after with
  `ab_builds.py` (constitution VIII).
- **Alternatives**: a Python wrapper per endpoint setting the variable (rejected: extra Python
  frame per request on the fast path); looking up the current task in the record factory
  (rejected: does not work for sync handlers on executor threads).

## R7. Structured logs

- **Decision**: `log_format` = `text` | `json` (default `text`). `init_logging(level, format)`
  builds the `fmt` layer with either the default text format or a Ferro `FormatEvent` that writes
  one JSON object per line using `serde_json` (already a dependency): `timestamp` (RFC 3339),
  `level`, `target`, `message` (when present) and every event field (`request_id`, `logger`,
  `method`, `status`, …). The format is fixed by the first initialization in a process.
- **Alternatives**: `tracing-subscriber`'s `json` feature (rejected: adds new crates; the needed
  formatter is ~80 lines); Python-side JSON formatting (rejected: engine logs would not be
  covered).

## R8. Metrics

- **Decision**: `ferro_http::metrics`, enabled by `metrics` (default false), served at
  `metrics_path` (default `/metrics`) on the serving port to client addresses in `metrics_allow`
  (default `127.0.0.1`, `::1`; IPs or CIDR ranges, parsed like `trusted_proxies`; the client is
  the address after trusted-proxy resolution). Others get the 404 envelope, as if absent.
- **Storage**: fixed arrays allocated at start: route slot (route id, plus one slot for
  unmatched paths) × method (GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS, other) × status class
  (1xx–5xx) counters, and per route slot × method a duration histogram (buckets 1 ms … 10 s) —
  only atomic adds on the request path, no locks, no allocation. Route labels are the route
  templates (`/items/{id}`) from the route table; probe and metrics requests are not counted.
- **Series**: `ferro_http_requests_total{route,method,status}`,
  `ferro_http_request_duration_seconds{route,method}` (histogram),
  `ferro_http_requests_in_flight`, `ferro_db_queries_total{outcome}` (`ok`, `error`,
  `unavailable`), `ferro_db_query_duration_seconds` (histogram),
  `ferro_process_start_time_seconds`, `ferro_build_info{version}`. Database series come from the
  bridge through `Dispatcher::write_metrics(&mut String)` (default: nothing); the bridge records
  them in its database completion path.
- **Format**: text exposition 0.0.4 (`text/plain; version=0.0.4`), label values escaped.
- **Multi-process**: per process (Feature 010 decision); a scrape reaches one worker. Documented.
- **Alternatives**: a separate metrics port (rejected for now: second listener and lifecycle;
  the allow-list keeps the default safe); OpenTelemetry (rejected: heavy dependency, out of MVP
  scope).

## R9. Stress and failure suite

- **Decision**: `tests/stress/` (added to `testpaths`), driving real `ferro run` subprocesses.
  `FERRO_STRESS=full` multiplies sizes and writes `tests/benchmarks/results/011-stress.md`. The
  PostgreSQL helper moves from `tests/python/orm/conftest.py` to `tests/support/postgres.py`;
  the stress suite starts its own temporary cluster so stopping it cannot disturb other tests.
- **Slow database without raw SQL**: SQLite — a standard-library `sqlite3` connection holds an
  exclusive lock on the same file; PostgreSQL — `psql` holds `LOCK TABLE … ACCESS EXCLUSIVE`
  inside a transaction.
- **Memory**: resident set size of the serving process (helper moved from `run_001.py` to
  `tests/support/process.py`), compared after warm-up and after the load.
- **Scenario map** (US3): 1 mixed concurrency + memory; 2 more connections than
  `max_connections`; 3 slow database vs request timeout (both databases); 4 PostgreSQL stopped
  and restarted under load (+ readiness); 5 queue full (`queue_size=8`) with a non-database route
  timed alongside; 6 body over/at the limit; 7 slow body → 408; 8 large result set repeated,
  memory steady; 9 client disconnect during a transaction (rollback, connection reusable with
  `pool_size=1`); 10 slow headers vs a normal client. Drain/shutdown (US2) and readiness (US1)
  scenarios live in the same suite.

## R10. Measurements

- SC-005: `tests/benchmarks/run_011.py` alternates hello world with `metrics` on and off
  (5 rounds, same build).
- Hot-path changes (request-id context, in-flight guard, metrics branch): before/after with
  `ab_builds.py` on hello world and the JSON route; a regression above 5% is investigated, above
  10% blocks (constitution VIII).

## R11. Client disconnects (found by the stress suite)

- **Finding**: Feature 001 assumed the engine drops a request's handler when the client goes
  away; the new disconnect scenario showed it does not — the handler ran to the end and committed
  its transaction. The engine's HTTP/1 connection keeps serving a request after the client closes
  its side, because it allows half-closed connections by default.
- **Decision**: disallow half-closed connections (`h1_allow_half_closed(false)` in
  `ferro_http::start_on`). A client that closes while its request runs ends the connection, which
  drops the handler: the bridge cancels the asyncio task and an open `transaction()` is rolled
  back. Regression test: `a_client_disconnect_cancels_the_handler` (engine) plus the stress
  scenario on SQLite and PostgreSQL.
- **Trade-off**: a client that half-closes its socket after sending a request and still expects
  the answer (rare, e.g. `nc -N`) now gets no response. Browsers, HTTP libraries and proxies do
  not do this. Documented in the settings contract.
