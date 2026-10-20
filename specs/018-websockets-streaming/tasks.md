---
description: "Task list for Feature 018 — WebSockets, Streaming Responses and Server-Sent Events"
---

# Tasks: WebSockets, Streaming Responses and Server-Sent Events

**Input**: Design documents from `specs/018-websockets-streaming/` (plan, spec, research,
data-model, contracts, quickstart)

**Tests**: Required (constitution VII); broadcast runs on memory and PostgreSQL (SQLite: a
configuration error, tested). See Implementation Notes for where the order deviated.

**Format**: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [X] T001 `actix-ws` 0.3 workspace dependency (used by `ferro-http` only); `tokio` `sync`/`macros` features where needed

## Phase 2: Foundational (engine-neutral types)

- [X] T002 `crates/core/src/stream.rs`: `ChunkSource`, `BodyStream`; `FerroResponse::with_stream`
- [X] T003 `crates/core/src/socket.rs`: `SocketMessage`, `SocketCommand`, `SocketEvent`, `SocketEvents`, `SocketLink` (outbound queue with 1 MiB limit and `Drained`, inbound limit 32), close codes; `Dispatcher::open_socket` (default: close 1011); `RouteOptions.websocket`
- [X] T004 `crates/http/src/lifecycle.rs`: stop signal, open-socket/stream counters and gauges (`ferro_websocket_connections`, `ferro_http_streams_open`, summed across processes)

## Phase 3: User Story 1 — WebSocket endpoints (P1) 🎯 MVP

- [X] T005 [P] [US1] Python tests `tests/python/test_realtime.py`: echo text/binary/JSON, path and query parameters (invalid → 1008), client leaving cancels a busy handler, close codes (1000, 1011 with log, app close, send after close), handshake checks (403 cross-origin, 426 plain GET), `ws.user` with `add_auth()`
- [X] T006 [US1] `crates/http/src/socket.rs` (`wants_upgrade`, connection task: frames ⇄ events, pings 20 s, silence 60 s, 1009, 1002, stop → 1001) and `service.rs` (`upgrade`: checks, 426, 101 with request id, metrics and access log)
- [X] T007 [US1] Engine tests `crates/http/tests/realtime.rs` (raw client in `tests/common/mod.rs`): echo, 426, cross-origin 403, rate limit 429, 1009, either side closes, stop → 1001
- [X] T008 [US1] Bridge: `Job::Socket`, `Socket` pyclass (`receive`/`send`/`close`, buffering, `Drained` wake-ups, cancellation rules), `Bridge::open_socket`, queued sockets closed when the bridge stops (`crates/python-bridge/src/{realtime.rs, bridge.rs, dispatch.rs, server.rs, lib.rs}`)
- [X] T009 [US1] Python: `python/ferro/websockets.py` (`WebSocket`, `WebSocketDisconnect`), `App.websocket`, `routing.build_websocket_endpoint` (parameters, 1008), `auth.sessions.read_session` for `ws.user`/`ws.session`, route registration without middleware (`app.py`, `routing.py`, `server.py`)
- [X] T010 [US1] `TestClient.websocket_connect`, `connect_websocket`, `TestWebSocket`, `WebSocketRejected` (`python/ferro/testing.py`)

## Phase 4: User Story 2 — Streaming responses and server-sent events (P1)

- [X] T011 [P] [US2] Python tests: chunks arrive as produced (with response middleware), SSE format and headers, ping comments, disconnect closes the generator (< 1 s, SC-003), a failing generator, events within 100 ms (SC-002), gauges
- [X] T012 [US2] Engine: `socket::chunks` (pull one chunk at a time, end on stop), streamed bodies in `service.rs`; tests in `realtime.rs` (pieces arrive over time, client leaving drops the source, stop ends streams)
- [X] T013 [US2] Bridge `PyChunks` (`__anext__` per chunk as an eager task; drop cancels and closes) and `streaming_response` conversion (`realtime.rs`, `types.rs`, `convert.rs`)
- [X] T014 [US2] `StreamingResponse`, `ServerSentEvent`, `EventStream` (`python/ferro/response.py`); `TestClient.stream` and `TestStreamResponse` (`iter_bytes`, `iter_lines`, `iter_events`)
- [X] T015 [US2] SSE over HTTP/2 in `tests/integration/test_tls.py`
- [X] T016 [US2] Regression: closing a generator suspended in an `await` (`test_disconnect_while_the_generator_waits`) — `aclose()` only after the cancelled chunk finished (`PyChunks`, `_event_lines`)
- [X] T017 [US2] Regression: a failing producer aborts the body instead of ending it cleanly (`ChunkSource` returns `Result`; `test_a_failing_stream_is_cut_off`, engine test `a_failing_source_aborts_the_body`)

## Phase 5: User Story 3 — Broadcast (P2)

- [X] T018 [P] [US3] Tests `tests/python/test_broadcast.py`: order and fan-out, copies, slow subscriber drops oldest, JSON and channel validation, 200 sockets < 1 s (SC-001), `database` needs PostgreSQL, PostgreSQL delivery, size limit, transaction commit/rollback
- [X] T019 [US3] `ferro_database::Listener`, `Backend::listen`; `crates/postgres/src/listener.rs` (dedicated connection, `LISTEN`, reconnect with back-off); bridge `broadcast_listen`/`broadcast_publish` (`pg_notify` through the transaction when one is open)
- [X] T020 [US3] `python/ferro/broadcast.py`; `broadcast` setting with the PostgreSQL check (`config.py`), `App(broadcast=)`, applied/stopped by the server (`server.py`)
- [X] T021 [US3] Integration test `tests/integration/test_realtime_processes.py` (SC-004: `--processes 2`, sockets on both workers receive one message)

## Phase 6: Polish

- [X] T022 Public API pinned (`tests/python/test_public_api.py`), `docs/stability.md`, `python/ferro/_ferro.pyi`
- [X] T023 `docs/guide/16-realtime.md` (examples verified by running them), index, README, routing/testing/metrics/scaling/settings/FAQ pages, `docs/deployment.md`, `deploy/nginx.conf` (upgrade headers), `deploy/Caddyfile` (`flush_interval -1`), CHANGELOG, roadmap
- [X] T024 `ferro routes` shows WebSocket routes as `WEBSOCKET`
- [X] T025 SC-005 measured with interleaved release builds
- [X] T026 Gates: fmt, clippy, cargo test, ruff, full pytest

## Implementation Notes

- **Test-first, partly**: the Python tests (T005, T011, T018) and the two-process test (T021)
  were written before the code they exercise. The engine's Rust tests (T007, T012) were written
  **after** the engine code (`socket.rs`, `chunks`) — a deviation from constitution VII; they
  were then run and pass. The later fixes (T016, T017) were test-first: each regression test was
  run red against the previous build.
- **Found while verifying the guide (T016)**: closing a generator suspended in an `await` (an
  SSE stream waiting for its next message when the client leaves) logged
  `aclose(): asynchronous generator is already running`, because the pending `__anext__` task
  had only been *asked* to cancel. `PyChunks` now closes the iterator from the cancelled task's
  completion callback, and `EventStream` waits for its inner task before `aclose()`. Cleanup
  (`finally`) ran in both versions; only the error log and the order were wrong.
- **Changed while documenting (T017)**: a failing generator first ended the body *normally*
  (`0\r\n\r\n`), so a cut-off CSV download looked complete. `ChunkSource::next_chunk` now returns
  `Result`, and a producer failure or a server stop aborts the body (HTTP/1.1: connection closed
  without the final chunk; HTTP/2: stream reset). actix discards unwritten output when a body
  fails, so the engine yields once before the error to let chunks already produced go out (the
  engine test lost `chunk 1` without it). The spec's clarification said streams "end" on stop;
  they are now cut off — clients reconnect either way.
- **SSE events**: the spec drafted dict events with `event`/`id` keys; implemented as
  `ServerSentEvent` objects (a dict yielded is JSON data), which avoids ambiguity with data that
  happens to have those keys. Spec acceptance scenario updated.
- **Access log**: one line per handshake (status 101) and per streamed response when its headers
  are sent — not when the stream finishes (FR-008 asked for "per finished stream"); stream
  duration is not logged. Recorded as a known gap.
- **`ferro routes`**: `App.routes` (public `RouteInfo`, a 3-tuple) was left unchanged so
  unpacking keeps working; the command reads the internal route records to print `WEBSOCKET`.
- **Measured** (SC-001…SC-005): 200 sockets receive one broadcast in < 1 s (SC-001, test); each
  SSE event within 100 ms of being yielded (SC-002, test); handler/generator ended < 1 s after
  the client leaves (SC-003, tests); with PostgreSQL and `--processes 2` a message reaches sockets
  on both workers (SC-004, integration test); plain HTTP unchanged within noise — hello world
  1.03×, JSON route 0.96×, database read 0.99× (medians of 5 interleaved rounds of release builds,
  rounds 0.78–1.30; [results](../../tests/benchmarks/results/018-realtime-ab.md)) (SC-005).
- **Environment**: building the old commit for the benchmark with the same `CARGO_TARGET_DIR`
  made cargo reuse its workspace crates for the new build (cargo hashes workspace paths
  relatively), failing with missing `Listener`; fixed with `cargo clean --release -p` for the
  workspace crates. The Rust-only baseline example was removed by that clean and not rebuilt.
- **Proxies**: Caddy's `encode` can hold back a streamed response until its first bytes
  (caddyserver/caddy#6293), so `deploy/Caddyfile` sets `flush_interval -1`; nginx gets the
  `Upgrade`/`Connection` map; the `database` broadcast backend's `LISTEN` connection needs
  PostgreSQL directly or PgBouncer in session mode (documented).
- **Gates**: see Feature 019's tasks (one combined run).
