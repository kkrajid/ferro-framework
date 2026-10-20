# Implementation Plan: WebSockets, Streaming Responses and Server-Sent Events

**Branch**: `018-websockets-streaming` | **Date**: 2026-10-12 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/018-websockets-streaming/spec.md`

## Summary

The engine learns two new kinds of response: an upgraded WebSocket connection, run by one task
per connection in `ferro-http` (actix-ws: handshake, frames, pings, limits, shutdown), and a
streamed body pulled chunk by chunk from a `ChunkSource`. `ferro-core` defines the engine-neutral
types (`SocketLink`, `SocketEvents`, `BodyStream`); the bridge implements them on the event loop
(`Socket` pyclass for handlers, `PyChunks` running `__anext__()` per chunk). Python adds
`@app.websocket`, `WebSocket`, `StreamingResponse`, `EventStream`/`ServerSentEvent`,
`ferro.broadcast` (memory, or PostgreSQL `LISTEN`/`NOTIFY` through a dedicated listener
connection in `ferro-postgres`) and test helpers ([research.md](research.md)).

## Technical Context

**Language/Version**: Rust 1.85+ (edition 2024); Python ≥ 3.12
**Primary Dependencies**: `actix-ws` 0.3 (new, in `ferro-http` only); `tokio` `sync`/`macros`
features in `ferro-core`/`ferro-http`
**Storage**: none new (broadcast uses PostgreSQL notifications, no tables)
**Testing**: `crates/http/tests/realtime.rs` (raw WebSocket client in `tests/common`),
`tests/python/test_realtime.py`, `tests/python/test_broadcast.py` (memory + PostgreSQL),
`tests/integration/test_realtime_processes.py` (2 processes), `tests/integration/test_tls.py`
(SSE over HTTP/2), `tests/python/test_public_api.py`
**Target Platform**: Windows 11 (development), Linux/macOS (CI)
**Performance Goals**: an event reaches the client within 100 ms of being yielded; 200 sockets
receive one broadcast within 1 s; plain HTTP requests unchanged (SC-005)
**Constraints**: no engine types in Python or the bridge's public surface; bounded memory per
connection in both directions; MSRV 1.85
**Scale/Scope**: 10 functional requirements, 3 user stories

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| I Thin Python, Rust core | PASS | Framing, pings, limits, backpressure, handshake checks and stream pumping in Rust; Python only formats SSE and fans out broadcast messages to local subscribers. |
| II Engine encapsulation | PASS | `actix-ws` used only in `ferro-http`; core/bridge see `SocketLink`, `SocketEvents`, `ChunkSource`. |
| III Framework-owned ORM | PASS | Broadcast `publish` runs through the database layer (inside a transaction it uses the transaction's connection). |
| IV Safe SQL | PASS | `SELECT pg_notify($1, $2)` with bound values; `LISTEN` on a fixed identifier. |
| V Secure by default | PASS | Handshake gets the cross-origin check (cross-site WebSocket hijacking) and rate limit; message size limit = `max_body_size`; bounded queues; 60 s silence closes. |
| VI Coarse boundary, non-blocking | PASS | One event-loop job per chunk or socket event; sends are queued synchronously, waiting only above 1 MiB. |
| VII Test-first + backend parity | PARTIAL | Python tests written first; the engine's Rust tests were written after its code (see tasks, Implementation Notes). Broadcast tested on memory and PostgreSQL (SQLite has no notifications: configuration error, tested). |
| VIII Measured performance | PASS | SC-005 measured with interleaved release builds (tasks, Implementation Notes). |
| IX Django-familiar | PASS | Channels-like `WebSocket` API (`receive`, `send`, `close`, `WebSocketDisconnect`); Starlette-like `StreamingResponse`. |
| X Scope discipline | PASS | Compression, subprotocols, WebSockets over HTTP/2, Redis broadcast out of scope (spec Assumptions). |

## Project Structure

```text
Cargo.toml (actix-ws workspace dependency)
crates/core/src/{stream.rs (ChunkSource, BodyStream), socket.rs (SocketLink, SocketEvents, close codes), response.rs (stream), dispatch.rs (open_socket), security/mod.rs (websocket routes), lib.rs}
crates/http/src/{socket.rs (wants_upgrade, run, chunks), service.rs (upgrade, streamed bodies), lifecycle.rs (stop signal, open sockets/streams), metrics.rs, snapshots.rs, server.rs}
crates/http/tests/{common/mod.rs (WsClient), realtime.rs}
crates/database/src/lib.rs (Listener, Backend::listen)
crates/postgres/src/{listener.rs, lib.rs}
crates/python-bridge/src/{realtime.rs, bridge.rs (Job::Socket), dispatch.rs, types.rs, errors.rs, convert.rs, request.rs, server.rs, lib.rs}
python/ferro/{websockets.py, broadcast.py, response.py (StreamingResponse, ServerSentEvent, EventStream), routing.py, app.py, server.py, auth/sessions.py (read_session), config.py (broadcast), testing.py, __init__.py, _ferro.pyi}
tests/python/{test_realtime.py, test_broadcast.py, test_public_api.py}, tests/integration/{test_realtime_processes.py, test_tls.py}
docs/guide/16-realtime.md, docs/{README.md, deployment.md, stability.md, roadmap.md}, deploy/{nginx.conf, Caddyfile}, CHANGELOG.md, README.md
```

## Complexity Tracking

| Violation | Why needed | Simpler alternative rejected because |
|---|---|---|
| A second connection per process for broadcast (`LISTEN`) | PostgreSQL delivers notifications only to a session that stays connected and idle-listening; pooled connections are returned after each query | Polling a table adds latency and write load; an external broker adds infrastructure the vision rules out for now |
