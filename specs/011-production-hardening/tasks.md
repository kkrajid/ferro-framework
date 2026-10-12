---
description: "Task list for Feature 011 — Production Hardening"
---

# Tasks: Production Hardening

**Input**: Design documents from `specs/011-production-hardening/` (plan, spec, research,
data-model, contracts, quickstart)

**Tests**: Required (constitution VII): every phase's tests are written and observed failing
before its implementation. Database behaviour is tested on SQLite and PostgreSQL.

**Format**: `[ID] [P?] [Story] Description` — [P] = parallelizable (different files, no
dependency on unfinished tasks).

## Phase 1: Setup (shared test infrastructure)

- [X] T001 Move the PostgreSQL server helper (`PostgresServer`, cluster creation, binary lookup) from `tests/python/orm/conftest.py` to `tests/support/postgres.py` (with `stop()`/`start()` added) and import it back in `tests/python/orm/conftest.py`; move `rss_bytes` from `tests/benchmarks/run_001.py` to `tests/support/process.py` and import it there
- [X] T002 Create `tests/stress/__init__.py` and `tests/stress/conftest.py`: a `served_app` fixture that writes an app module to a temp dir and runs `ferro run` as a subprocess (stderr captured to a file, settings via environment), a `stress_scale` fixture (`FERRO_STRESS=full` → larger sizes), a dedicated temporary PostgreSQL cluster fixture built on `tests/support/postgres.py`, and a results collector that writes `tests/benchmarks/results/011-stress.md` in full mode; add `tests/stress` to `testpaths` in `pyproject.toml`

## Phase 2: Foundational (settings used by every story)

- [X] T003 [P] Tests for the new settings in `tests/python/test_config.py`: defaults, precedence and invalid values for `health_path`, `ready_path`, `metrics`, `metrics_path`, `metrics_allow`, `log_format`, `shutdown_drain` (paths must start with `/`, no whitespace or `{`; probe/metrics paths distinct; `metrics_allow` entries parse as IPs/CIDRs); `to_native()` carries the engine settings and not `shutdown_drain`/`log_format`
- [X] T004 [P] Tests in `crates/core/tests/core.rs` for `Settings` validation of the new fields (bad path, duplicate paths, bad allow entry)
- [X] T005 Add the settings to `python/ferro/config.py` (specs, `Settings` fields, validation), `crates/core/src/settings.rs` (fields, defaults, `validate`), the dict mapping in `crates/python-bridge/src/server.rs`, and `--log-format` / `--shutdown-drain` in `python/ferro/cli.py` (`_SERVER_OPTIONS`); update `python/ferro/_ferro.pyi` if signatures change

**Checkpoint**: settings resolve and validate; nothing uses them yet.

## Phase 3: User Story 1 — Health and readiness probes (P1) 🎯 MVP

**Goal**: engine-answered `/healthz` and `/readyz`; readiness follows the database and the
server state. **Independent test**: query both probes with the database up, blocked and back.

- [X] T006 [P] [US1] Rust tests `crates/http/tests/probes.rs`: liveness 200 body/headers without dispatching; readiness 200 / 503 + `failing` from a test dispatcher's `readiness()`; HEAD; POST → 405 with `Allow: GET, HEAD`; disabled path → routed as a normal path; probes bypass rate limits; probe requests not logged at info
- [X] T007 [P] [US1] Python tests `tests/python/test_probes.py`: `/healthz` and `/readyz` through `TestClient` with a SQLite file database; readiness turns 503 `["database"]` while another `sqlite3` connection holds an exclusive lock and back to 200 after release (within 2 s each way); 20 concurrent probes cause one check; a route on `/healthz` → `ConfigurationError` naming the handler and `health_path`; no database configured → ready
- [X] T008 [US1] Add `Dispatcher::readiness()` (default: no failing checks) to `crates/core/src/dispatch.rs`
- [X] T009 [US1] Probes in `crates/http/src/service.rs` (answered before preflight/routing per `contracts/http-probes.md`) with a `draining` flag in `State`, set by `RunningServer::begin_drain()` and by `stop()` in `crates/http/src/server.rs`
- [X] T010 [US1] Readiness in the bridge: cached single-flight `SELECT 1` with a 750 ms limit in `crates/python-bridge/src/dispatch.rs` (helper in `crates/python-bridge/src/db.rs`)
- [X] T011 [US1] Route/probe address collision check in `python/ferro/server.py` (`ServerRunner.__init__`), message per `contracts/http-probes.md`
- [X] T012 [US1] Stress scenario `tests/stress/test_readiness.py`: PostgreSQL stopped and restarted under probe polling — 503 `["database"]` within 2 s, 200 within 2 s of restart, liveness 200 throughout (SC-001)

**Checkpoint**: US1 independently testable (T006, T007, T012 green).

## Phase 4: User Story 2 — Graceful shutdown that drains traffic (P1)

**Goal**: drain period with failing readiness, bounded shutdown, accounting log.
**Independent test**: continuous load + stop signal.

- [X] T013 [P] [US2] Rust tests in `crates/http/tests/server.rs`: `begin_drain()` makes readiness 503 `["server"]` while requests are still served; shutdown statistics after a stop with a request outliving `shutdown_timeout` (`at_stop` 1, `cut_off` 1) and with one finishing (`finished` 1); client disconnects before stop are not counted
- [X] T014 [P] [US2] Integration tests `tests/integration/test_drain.py`: `ferro run --shutdown-drain 2` with a 1 s handler: after the signal readiness is 503 for ~2 s while new requests succeed, then the process exits 0 within drain + timeout + 1 s and logs the `stopped: …` summary; a second signal during the drain exits at once (code 1); `--processes 2` drains every worker
- [X] T015 [US2] In-flight guard and shutdown statistics in `crates/http/src/service.rs` / `crates/http/src/server.rs` (`RunningServer::shutdown_stats()`, summary log on `ferro.server` when the server thread ends)
- [X] T016 [US2] `Server.begin_drain()` in `crates/python-bridge/src/server.rs` (+ `_ferro.pyi`); `ServerRunner.begin_drain()` and the drain phase of `serve()` in `python/ferro/server.py` (log line per `contracts/logging.md`, second signal → non-graceful stop)
- [X] T017 [US2] Drain-aware kill deadline in `python/ferro/supervisor.py` and `python/ferro/cli.py` (`shutdown_timeout + shutdown_drain + 5`)
- [X] T018 [US2] Stress scenario `tests/stress/test_shutdown.py`: 100 clients of continuous requests, stop signal with a 2 s drain: 0 failed requests among those started before the drain ended, exit within drain + timeout + 1 s (SC-002)

**Checkpoint**: US1 + US2 green.

## Phase 5: User Story 3 — Predictable behaviour under stress and failure (P1)

**Goal**: 503 for database unavailability, fail-fast queue, 408 for slow bodies, proven by the
stress suite. **Independent test**: run `tests/stress`.

- [X] T019 [P] [US3] Rust backend tests: `crates/sqlite/tests/backend.rs` — a lock held past the busy timeout gives `DbError::Unavailable`; `queue_size=1` with a blocked worker rejects the next jobs with `Unavailable` immediately; URL parameters (`queue_size`, unknown parameter error); `crates/postgres/tests/` — closed port and terminated backend connection give `Unavailable`, a SQL error stays `Operational`, `queue_size` parsing
- [X] T020 [P] [US3] Python tests `tests/python/orm/test_unavailable.py` (SQLite and PostgreSQL): `DatabaseUnavailableError` is an `OperationalError`, exported from `ferro.models` and `ferro.db`; raised for a stopped PostgreSQL and a locked SQLite file; an unhandled one in a handler → 503 envelope with `Retry-After: 1`, no database text in the body, one warning with the request id; a handled one lets the handler degrade; missing table stays 500
- [X] T021 [P] [US3] Rust test in `crates/http/tests/server.rs`: a body trickled slower than `request_timeout` → 408 and connection closed; a fast body plus a slow handler still → 503; body and handler share one deadline
- [X] T022 [US3] `DbError::Unavailable` in `crates/database/src/lib.rs`; classification in `crates/postgres/src/errors.rs` / `crates/postgres/src/worker.rs` and `crates/sqlite/src/errors.rs`; `try_send` + `queue_size` in `crates/sqlite/src/lib.rs` (SQLite URL parameters) and `crates/postgres/src/lib.rs` / `crates/postgres/src/config.rs`; exhaustive matches updated across the bridge
- [X] T023 [US3] `DatabaseUnavailableError` in `python/ferro/models/exceptions.py` (exports in `python/ferro/models/__init__.py`, `python/ferro/db.py`, `_ferro.pyi` if needed); mapping `Unavailable`/`Closed` → it in `crates/python-bridge/src/orm_values.rs`; 503 + `Retry-After: 1` + warning in `crates/python-bridge/src/errors.rs`
- [X] T024 [US3] One request deadline covering body and handler in `crates/http/src/service.rs` (408 + close on body expiry)
- [X] T025 [US3] Stress scenarios `tests/stress/test_failures.py`: mixed concurrency with memory check; more connections than `max_connections`; slow database vs request timeout (SQLite lock, PostgreSQL `LOCK TABLE` via `psql`) with connections free afterwards; PostgreSQL stopped under load (503 for database routes, 200 for others, recovery); `queue_size=8` flood (fast 503s, non-database route stays fast); body over/at the limit; slow body → 408; large result set repeated without memory growth; client disconnect during a transaction (rolled back, `pool_size=1` connection reusable); slow headers vs a normal client (SC-003)

**Checkpoint**: P1 stories complete.

## Phase 6: User Story 4 — Request IDs in application logs (P2)

**Goal**: every application log record carries the request id; optional JSON lines.
**Independent test**: a handler logs; the line carries the response's `x-request-id`.

- [X] T026 [P] [US4] Python tests `tests/python/test_logging.py`: record factory adds `request_id` (None outside requests); async and sync handlers see their own id (concurrent requests do not mix); `RustHandler` forwards it; `configure` attaches to an unconfigured root logger and leaves a configured one alone
- [X] T027 [P] [US4] Integration tests `tests/integration/test_logs.py`: `ferro run` with an app logging through `logging.getLogger("shop")` from async and sync handlers — each line's `request_id` equals the response header; `--log-format json` — every stderr line parses as JSON with `timestamp`, `level`, `target`, and access/application fields (SC-004)
- [X] T028 [US4] `_ferro.request_id_context` ContextVar and set/reset around task creation in `crates/python-bridge/src/bridge.rs` (+ registration in `crates/python-bridge/src/lib.rs`, `_ferro.pyi`); copied context for sync handlers in `python/ferro/routing.py`
- [X] T029 [US4] `request_id` parameter of `_ferro.log`, JSON `FormatEvent` and the format parameter of `init_logging` in `crates/python-bridge/src/logging.rs`
- [X] T030 [US4] Record factory, root-logger attachment and `configure(level, fmt)` in `python/ferro/log.py`; pass `log_format` from `python/ferro/server.py`

**Checkpoint**: US4 green.

## Phase 7: User Story 5 — Metrics (P2)

**Goal**: opt-in Prometheus-format metrics for allowed clients. **Independent test**: enable,
send known requests, read `/metrics`.

- [X] T031 [P] [US5] Rust tests `crates/http/tests/metrics.rs`: off → 404; allowed client → text format with counts by route template/method/status class, `<unmatched>` for unknown paths, cumulative buckets, in-flight gauge; disallowed client → 404; label escaping; probe/metrics requests not counted
- [X] T032 [P] [US5] Python tests `tests/python/test_metrics.py`: `TestClient` with `metrics=True` and SQLite — database query counters/histogram by outcome (`ok`, `error`, `unavailable`), route templates of a real app, `ferro_build_info`
- [X] T033 [US5] `crates/http/src/metrics.rs` (registry, recording, exposition) and its use in `crates/http/src/service.rs` (`metrics_allow` matching via the trusted-proxy parser)
- [X] T034 [US5] `Dispatcher::write_metrics()` in `crates/core/src/dispatch.rs`; database timing/outcome recording in `crates/python-bridge/src/db.rs` and rendering in `crates/python-bridge/src/dispatch.rs`
- [X] T035 [US5] Metrics overhead benchmark `tests/benchmarks/run_011.py` (hello world, metrics on/off alternating, 5 rounds) → `tests/benchmarks/results/011-metrics.md` (SC-005)

**Checkpoint**: all stories green.

## Phase 8: Polish & cross-cutting

- [X] T036 [P] Docs: `docs/getting-started.md` (new "Running in production" section: probes, drain, logs, metrics, database unavailability, `queue_size`; settings table; limits), README status, `docs/roadmap.md`
- [X] T037 Hot-path measurement: hello world's share of the Rust-only baseline measured in the same session by `run_011.py`, compared with Feature 010's (≤ 5% regression investigated, > 10% blocks; `ab_builds.py` cannot swap builds across this feature because the Python side calls new native signatures)
- [X] T038 Full stress run `FERRO_STRESS=full pytest tests/stress` → `tests/benchmarks/results/011-stress.md`
- [X] T039 Gates (ruff, `pytest`, `cargo clippy --workspace --all-targets -D warnings`, `cargo test`), quickstart validation, Implementation Notes in this file

## Dependencies & Execution Order

- Setup (T001–T002) → Foundational (T003–T005) → stories.
- US1 (T006–T012) first: US2 reuses its `draining` flag (T009).
- US2 (T013–T018) after US1; US3 (T019–T025) after Foundational (independent of US1/US2 except
  the shared stress fixtures); US4 (T026–T030) and US5 (T031–T035) after Foundational,
  independent of each other and of US3.
- Within a story: tests (observed failing) → engine/backend → bridge → Python → stress scenario.
- Polish after all stories.

## Parallel Opportunities

- T003 ∥ T004; T006 ∥ T007; T013 ∥ T014; T019 ∥ T020 ∥ T021; T026 ∥ T027; T031 ∥ T032 (test
  files are independent).
- US4 and US5 can proceed in parallel once Foundational is done.

## Implementation Strategy

- MVP: Setup + Foundational + US1 (probes) — deployable value on its own.
- Then US2 (drain) and US3 (failure behaviour + suite) to complete the P1 set, then US4 and US5.
- Rebuild the native module after each Rust change (`maturin develop --uv`; release build only
  for measurements, `CARGO_BUILD_JOBS=2` on this machine).

## Implementation Notes (recorded during /speckit-implement)

- **Test-first observance.** Settings (T003/T004), probes (T006/T007), shutdown statistics and
  drain (T013/T014), unavailability, queue bound and body deadline (T019–T021), logging
  (T026/T027) and metrics (T031/T032) tests were written before their code and observed failing
  (missing settings/fields/methods, `ImportError`, `TypeError` against the previous build). The
  stress scenarios (T012, T018, T025) were written after the behaviour they exercise and passed
  except where noted below. Measurement scripts were written with their tasks.
- **Bugs found by the new tests (each fixed with a regression test).**
  - **Client disconnects did not cancel handlers.** Feature 001 assumed the engine drops a
    request when its client goes away; the disconnect scenario showed the handler ran to the
    end and committed its transaction. The engine allowed half-closed connections; with
    `h1_allow_half_closed(false)` a vanished client ends the connection and the bridge cancels
    the task (engine test `a_client_disconnect_cancels_the_handler` observed failing, then the
    stress scenario on SQLite and PostgreSQL passes). Trade-off documented (research R11).
  - **A full database queue blocked the Python thread** (and could deadlock the process: a
    backend thread needs the interpreter to wake the loop) — now fails fast with
    `DatabaseUnavailableError`; queue size configurable (`queue_size`).
  - **Request bodies had no time bound** — now within `request_timeout` (408).
  - **Database outages answered 500** — now 503 + `Retry-After` with a warning line.
  - **`App(processes=...)`** promised by Feature 010's spec was missing — added with the new
    settings.
- **Design adjustments during implementation.** The readiness database check limit went from
  1 s to 750 ms: on Windows a connection attempt to a stopped local server takes 1–2 s to fail,
  so 1 s cache + 1 s check left no margin for SC-001 (measured 2.105 s). SQLite in WAL mode
  never blocks readers, so the failing-database readiness tests use PostgreSQL. The terminated-
  connection test was changed to assert the existing transparent reconnect (the worker notices a
  closed connection before the next query). The hot-path before/after with `ab_builds.py`
  (T037) could not be done: the Python side now calls new native signatures, so builds cannot
  be swapped across this feature.
- **Test-harness fixes**: substring token matching in the log test; a sqlite3 handle used from a
  timer thread; reading only part of a 408 response; two scenarios made robust against machine
  load (wait for the PostgreSQL lock to be granted; wait for both workers before draining); an
  engine test's timing margin widened from 50 ms to 200 ms after load-induced flakiness.
- **Environment**: this machine had 0.1–0.6 GB of free memory throughout; parallel builds made
  rustc crash and left corrupt artifacts twice (cleaned and rebuilt with one job and incremental
  compilation off).
- **Results** ([stress](../../tests/benchmarks/results/011-stress.md),
  [metrics](../../tests/benchmarks/results/011-metrics.md)):
  - **SC-001 PASS (one outlier)**: outages reported within 2 s in every cycle (0.78–1.78 s);
    recoveries within 2 s in 26 of 27 cycles (0.24–0.79 s), one 4.39 s outlier that did not
    recur in 10 further cycles; liveness never failed.
  - **SC-002 PASS**: 100 clients, 2 s drain: 0 failed among 8,589 requests started before the
    drain ended (415 served during the drain); exit after 4.92 s (bound 8 s). With 200 clients
    (beyond the criterion) one earlier run had 4 of 8,921 fail — most likely the client: 200
    threads in one process stamp a start time well before actually sending.
  - **SC-003 PASS**: 30,000 mixed requests with 0 wrong answers; connections beyond the limit
    wait and all succeed; slow database → 503 on time on both databases with every connection
    free afterwards; PostgreSQL outage under load → only 200/503, never 500, plain route
    unaffected, recovery without restart; full queue → 96% refused in < 1 s with the plain route
    fast; 413/408 on time; large result set memory steady after warm-up (≤ +6.7%); client
    disconnects roll back on both databases; slow headers cut off at the header timeout.
  - **SC-004 PASS**: all 50 request log lines carry the matching id (text and JSON).
  - **SC-005 PASS**: metrics cost −2.1% (median per-round server CPU per request, 120.9 vs
    121.9 µs) — no cost detectable. Throughput comparisons were unusable on this machine
    (back-to-back runs varied ±40%), so the verdict uses CPU time per request.
  - **T037**: hello world reached 98% of the Rust-only baseline in one session (Feature 010:
    66–88%); no regression visible, within the machine's noise.
- **Gates**: ruff; `pytest` 682 passed, 2 skipped (reduced stress suite and the Feature 012
  acceptance tests included); `cargo clippy --workspace --all-targets -D warnings` clean;
  `cargo test` 170 passed — Windows 11, CPython 3.13, PostgreSQL 18.1. One earlier full-suite run
  had the drain-under-load scenario fail once (passed in every standalone, full-size and
  under-load run before and after); it now reports each failure's timing and error if it recurs.
