# Research: WebSockets, Streaming Responses and Server-Sent Events

## R1 — WebSocket protocol handling in the engine

**Decision**: `actix-ws` 0.3 (actix-web's actor-free WebSocket support). It performs the
handshake, returns the `101` response, a `Session` (thread-safe sender of frames) and a
`MessageStream` (frames from the client, bound to the worker thread), with `max_frame_size` and
continuation aggregation.

**Rationale**: Same engine, no actor framework, maintained by the actix team; only `ferro-http`
depends on it (constitution II).

## R2 — Engine-neutral socket interface

**Decision**: `ferro-core` defines `SocketMessage`, `SocketEvent` (message, drained, closed),
a `SocketEvents` trait implemented by the dispatcher, and `SocketLink` — a shared object holding
the outbound queue (application → client), byte and message counters and two `Notify`s. The
engine runs one task per connection that reads frames, delivers events, writes queued commands,
pings, and reacts to shutdown. `Dispatcher::open_socket(request, link)` returns the events sink.

**Rationale**: Engine types never reach Python or the bridge. Backpressure in both directions
needs no async runtime on the Python side: outbound sends are queued synchronously and the
application is told when the queue drains below its limit (`Drained`); inbound frames are read
only while fewer than 32 messages wait for the application.

## R3 — Streaming bodies: pull-based

**Decision**: `FerroResponse` gains an optional `BodyStream` (a `ChunkSource` with
`next_chunk()`); the engine asks for the next chunk only after the client accepted the previous
one. The bridge implements a chunk request as one job on the event loop: `__anext__()` of the
application's async iterator, run as an eager task. Dropping the source (client gone, shutdown)
cancels a pending chunk and calls `aclose()` once that cancellation has completed (a generator
cannot be closed while its `__anext__` runs). A producer failure (or a stop before the end)
is reported as `Err(StreamAborted)`, and the engine aborts the body instead of ending it, so
a cut-off body never looks complete.

**Rationale**: Natural backpressure (a slow client slows the generator instead of filling
memory), one round trip per chunk (cheap next to network I/O), and generator cleanup always runs.

**Alternatives**: a bounded channel filled by a Python task (needs an awaitable Rust send from
Python and still a separate cancellation path).

## R4 — Server-sent events

**Decision**: Implemented in Python on top of streaming responses: `EventStream` formats events
(`event:`, `id:`, `retry:`, one `data:` line per line of data; dicts/lists JSON-encoded), sets
`content-type: text/event-stream`, `cache-control: no-cache`, `x-accel-buffering: no` (nginx), and
emits `: ping` comments after 15 s without an event.

## R5 — Disconnects, errors and shutdown

**Decision**: Mirrors HTTP: a socket client leaving cancels the handler unless it is waiting in
`receive()`, which raises `WebSocketDisconnect(code, reason)` instead (so `async for` loops end
cleanly). Handler exceptions close with 1011 and are logged with the request id. On server stop,
`Lifecycle` notifies sockets (close 1001) and streams (end); then the normal graceful shutdown
runs. Silent connections: ping every 20 s, close after 60 s without any frame.

## R6 — Handshake security

**Decision**: The handshake goes through the same rate limit and cross-origin check as unsafe
requests (the check is told the method is `WEBSOCKET`, which is never "safe"). Browsers always send
`Origin` (and `Sec-Fetch-Site`) on WebSocket handshakes, so cross-site pages are refused while
scripts and apps without browser metadata connect. Messages larger than `max_body_size` close
with 1009.

## R7 — Broadcast

**Decision**: `ferro.broadcast` — `memory` backend: per-channel sets of bounded asyncio queues
(1,000 messages; oldest dropped and counted). `database` backend on PostgreSQL: one dedicated
listener connection per process (`LISTEN ferro_broadcast`, run on the PostgreSQL backend's
runtime, reconnecting with backoff); `publish` runs `SELECT pg_notify('ferro_broadcast', $1)`
with a JSON payload `{"c": channel, "m": message}`; every process receives every notification
and delivers it to local subscribers. Payloads are limited to 7,000 bytes (PostgreSQL's limit is
8,000).

**Rationale**: No new infrastructure for teams already on PostgreSQL; ordering per publisher is
preserved by PostgreSQL. SQLite has no equivalent, so `database` broadcast on SQLite is a startup
error rather than silent per-process behaviour.

## R8 — Testing support

**Decision**: `TestClient.websocket_connect(path)` — a small standard-library WebSocket client
(handshake, masked frames, ping/close handling) — and `TestClient.stream(method, path)` yielding a
response with `iter_bytes()`, `iter_lines()` and `iter_events()` (SSE parser). Rust engine tests
use an equivalent raw client in `crates/http/tests/common`.
