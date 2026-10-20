# Data Model: WebSockets, Streaming Responses and Server-Sent Events

No database tables. The entities are in-memory objects and wire formats.

## Engine (ferro-core, engine-neutral)

| Entity | Fields / members | Rules |
|---|---|---|
| `SocketMessage` | `Text(String)` \| `Binary(Bytes)` | size limit = `max_body_size` (larger: close 1009) |
| `SocketCommand` | `Send(SocketMessage)` \| `Close { code, reason }` | written by the connection task in order |
| `SocketEvent` | `Message(SocketMessage)` \| `Drained` \| `Closed { code, reason }` | delivered to `SocketEvents::event`, never blocking an engine thread |
| `SocketLink` | outbound queue, queued bytes, inbound count, closed flag, two `Notify`s | `send` refuses after close (`SocketClosed`), reports "full" at `OUTBOUND_LIMIT` (1 MiB); `Drained` below half; reading pauses at `INBOUND_LIMIT` (32) unconsumed messages |
| close codes | 1000 normal, 1001 going away (shutdown), 1002 protocol error, 1006 abnormal (no close frame / silence), 1008 policy (invalid parameters), 1009 too big, 1011 internal (handler raised) | application codes: 1000, 1001, 1003, 1007, 1008, 1011, 3000–4999; reason ≤ 123 bytes |
| `ChunkSource` | `next_chunk() -> Future<Option<Bytes>>` | `None` ends the body; dropping it closes the producer |
| `BodyStream` | shared `Option<Box<dyn ChunkSource>>` | taken once by the engine (`take()`); `FerroResponse.stream` |

## Connection life cycle (ferro-http)

```text
GET + Upgrade ──► route is websocket? ─no─► normal HTTP
                       │yes
             rate limit / cross-origin check ──fail──► 429 / 403 (no upgrade)
                       │
             Upgrade header? ─no─► 426 Upgrade Required
                       │
             101 + task: read frames ⇄ SocketEvents, write SocketCommands,
                         ping every 20 s, close after 60 s silent,
                         stop signal ──► close 1001
```

`Lifecycle` counts open sockets and streams (gauges `ferro_websocket_connections`,
`ferro_http_streams_open`, summed across worker processes) and notifies them on stop.

## Bridge (ferro._ferro)

| Entity | Role |
|---|---|
| `Socket` | handler-facing connection: `receive()`, `send()`, `close()`, `closed`, `close_info`, `request`; buffers inbound messages, holds futures of a waiting `receive()` and of `send()`s waiting for `Drained` |
| `PyChunks` | `ChunkSource` over a Python async iterator: each chunk is one `__anext__()` task on the event loop; on drop, a chunk in progress is cancelled and the iterator is closed (`aclose()`) once that task has finished |
| `BroadcastListener` | handle of the PostgreSQL listener; `stop()` |

Handler outcome: returns → close 1000; `WebSocketDisconnect` raised → normal end; other exception
→ close 1011 and log; client closes → cancel the handler unless it waits in `receive()` or
closed the socket itself.

## Python API objects

| Entity | Fields |
|---|---|
| `WebSocket` | handshake request attributes (`path`, `raw_path`, `query`, `query_string`, `path_params`, `headers`, `cookies`, `client`, `request_id`), `state`, `user`, `session` (read-only mapping), `closed`, `close_code`, `close_reason` |
| `WebSocketDisconnect` | `code`, `reason` |
| `StreamingResponse` | status, headers, media type, `_iterator` (async iterator over `str`/`bytes`) |
| `ServerSentEvent` | `data` (str as is, other values JSON), `event`, `id`, `retry` (ms, ≥ 0), `comment`; line breaks refused in `event`/`id`/`comment` |
| `EventStream` | `StreamingResponse` with `text/event-stream`, `cache-control: no-cache`, `x-accel-buffering: no`, `: ping` after `ping` seconds of silence (default 15; `None` disables) |
| `Subscription` | `channel`, `dropped`, queue of up to 1,000 messages (oldest dropped) |
| `Broadcast` | `backend` (`memory` \| `database`), `explicit` (chosen with `configure`), channel → subscriptions |

## Broadcast wire format (database backend)

PostgreSQL channel `ferro_broadcast`; payload = JSON `{"c": "<channel>", "m": <message>}`, at most
7,000 bytes encoded (PostgreSQL's limit is 8,000). Every process receives every notification and
delivers it to its own subscribers of `c`. A notification sent inside a transaction is delivered
at commit (dropped on rollback).

## Setting

| Setting | Values | Validation |
|---|---|---|
| `broadcast` (`FERRO_BROADCAST`, `App(broadcast=)`) | `memory` (default), `database` | `database` with a non-PostgreSQL `database_url` → `ConfigurationError` at start |
