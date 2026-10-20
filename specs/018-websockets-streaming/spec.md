# Feature Specification: WebSockets, Streaming Responses and Server-Sent Events

**Feature Branch**: `018-websockets-streaming`

**Created**: 2026-10-12

**Status**: Implemented (see [tasks.md](tasks.md), Implementation Notes)

**Input**: User description: "ee ferro yill websocket, see features undeeo? … yes start" — add
real-time features: WebSocket endpoints, server-sent events (SSE) and streaming responses.

## Clarifications

### Session 2026-10-12

The user asked to start without further questions; recommended options were taken.

- Q: SSE only, or WebSockets too? → A: Both, plus general streaming responses (SSE is a
  streaming response with a text format), and a publish/subscribe helper so handlers can push
  messages to many connections.
- Q: What does a WebSocket handler look like? → A: `@app.websocket("/ws/{room}")` on an
  `async def handler(ws, room: str)`; `await ws.receive()` (or `async for message in ws`),
  `await ws.send(text_or_bytes)`, `send_json`/`receive_json`, `await ws.close(code, reason)`. Path
  and query parameters work as for HTTP routes. The connection is accepted before the handler
  runs; returning from the handler closes it normally.
- Q: Security of the handshake? → A: The handshake gets the same engine checks as an unsafe HTTP
  request: rate limit, and the cross-origin check (browsers send `Origin`; a page on another site
  cannot open a socket that carries the user's cookies — cross-site WebSocket hijacking). Route
  options `rate_limit` and `csrf=False` apply. With authentication on, `ws.user` and `ws.session`
  (read-only) are loaded from the session cookie.
- Q: Limits and dead connections? → A: Incoming messages larger than `max_body_size` close the
  connection (1009). The server pings every 20 s and closes connections silent for 60 s. Messages
  the application has not yet received are bounded (the server stops reading from a client whose
  messages are not consumed); messages being sent are bounded too (`send` waits when 1 MiB is
  queued).
- Q: What happens when the client goes away? → A: A handler waiting in `receive()` gets
  `WebSocketDisconnect`; a handler waiting on anything else is cancelled (like an HTTP handler whose
  client disconnects). `send` after the close raises `WebSocketDisconnect`.
- Q: Streaming responses? → A: `StreamingResponse(async_iterable, media_type=...)` sends each
  `bytes`/`str` chunk as produced (backpressure: the next chunk is requested when the client took
  the previous one). `EventStream(async_iterable)` formats server-sent events (`data`, `event`,
  `id`, `retry`), sets the SSE headers and sends a keep-alive comment every 15 s of silence.
  `request_timeout` covers the handler until it returns the response object, not the stream.
- Q: Shutdown? → A: On stop (after the drain), open sockets are closed with 1001 (going away) and
  streams end, so clients reconnect elsewhere; the generator's `finally` blocks run.
- Q: Pub/sub across processes and machines? → A: `ferro.broadcast` with `publish(channel,
  message)` and `subscribe(channel)`; backend `memory` (one process, default) or `database`
  (PostgreSQL LISTEN/NOTIFY: every process and machine on the same database; messages up to 7,000
  bytes of JSON). SQLite has no notification mechanism, so `database` broadcast requires
  PostgreSQL.
- Q: HTTP/2? → A: Streaming responses and SSE work over HTTP/1.1 and HTTP/2; WebSockets use
  HTTP/1.1 upgrades (browsers open them so even when the page uses HTTP/2).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - WebSocket endpoints (Priority: P1)

A developer builds a live feature (chat, notifications, collaborative editing) with a two-way
connection.

**Independent Test**: An echo endpoint answers text, binary and JSON messages; a disconnect ends
the handler; the handshake is refused from another origin.

**Acceptance Scenarios**:

1. **Given** `@app.websocket("/echo")`, **When** a client connects and sends `"hi"`, **Then** it
   receives what the handler sends; binary and JSON round-trip too.
2. **Given** path/query parameters with type hints, **Then** they are converted; invalid ones
   close the connection with 1008 and the problem in the reason.
3. **Given** a browser page on another site, **When** it opens a socket, **Then** the handshake
   gets 403; same-origin pages, trusted origins and non-browser clients connect.
4. **Given** authentication, **Then** `ws.user` is the logged-in user (anonymous otherwise).
5. **Given** the client closes, **Then** a handler in `receive()` gets `WebSocketDisconnect` with
   the code; a handler awaiting something else is cancelled.
6. **Given** a handler that raises, **Then** the socket closes with 1011 and the error is logged
   with the request id.
7. **Given** a message larger than `max_body_size`, **Then** the connection closes with 1009.
8. **Given** a plain GET to a WebSocket route, **Then** 426 with `upgrade: websocket`.
9. **Given** the server stops, **Then** sockets close with 1001.

---

### User Story 2 - Streaming responses and server-sent events (Priority: P1)

A developer pushes progress or notifications to a browser with SSE, or streams a large export
without building it in memory.

**Independent Test**: An SSE endpoint sends three events with ids; the client reads them as they
arrive; disconnecting runs the generator's cleanup.

**Acceptance Scenarios**:

1. **Given** `EventStream(gen())`, **Then** the response has `content-type: text/event-stream`,
   `cache-control: no-cache`, and each yielded value arrives as an event when yielded (not at the
   end).
2. **Given** `ServerSentEvent` values with `event`, `id`, `retry` and multi-line `data`, **Then**
   they are formatted per the SSE specification; other yielded values are the data (`str` as
   is, dicts/lists and other JSON values as JSON).
3. **Given** a quiet stream, **Then** a comment line is sent every 15 s.
4. **Given** `StreamingResponse(chunks(), media_type="text/csv")`, **Then** chunks are sent
   with chunked transfer (HTTP/1.1) or as DATA frames (HTTP/2).
5. **Given** the client disconnects, **Then** the generator is closed (its `finally` runs).
6. **Given** the generator raises, **Then** the body is aborted (the client sees an incomplete
   response, not a complete-looking one) and the error is logged.
7. **Given** the server stops, **Then** streams are cut off the same way and generators are
   closed.

---

### User Story 3 - Broadcast to many connections (Priority: P2)

A chat room: a message from one connection reaches every connection in the room, whichever
process (and, with PostgreSQL, whichever machine) holds it.

**Independent Test**: Two subscribers on a channel both receive a published message; with
`database` broadcast on PostgreSQL, a message published in one process reaches a subscriber in
another.

**Acceptance Scenarios**:

1. **Given** `async with broadcast.subscribe("room:1") as messages`, **When** another task
   publishes, **Then** every subscriber receives it once, in order.
2. **Given** a subscription that ended, **Then** it receives nothing more and holds no memory.
3. **Given** `broadcast="database"` on PostgreSQL and two processes, **Then** messages cross
   processes; non-JSON or oversized messages raise `ValueError`/`TypeError` naming the limit.
4. **Given** `broadcast="database"` on SQLite, **Then** a configuration error at start.

### Edge Cases

- A slow subscriber: its queue is bounded (1,000 messages); beyond that the oldest are dropped and
  the subscription records how many (`dropped`), so one slow client cannot exhaust memory.
- Many sockets per process: each costs one Python task and small buffers; `max_connections`
  bounds them.
- Testing: `TestClient.websocket_connect(path)` and `TestClient.stream(...)` let applications test
  sockets and streams.

## Requirements *(mandatory)*

- **FR-001**: `app.websocket(path, *, name=None, rate_limit=None, csrf=True)`; handler parameter
  `ws` (or annotated `WebSocket`) plus path/query parameters.
- **FR-002**: `WebSocket`: `receive()`, `receive_text()`, `receive_bytes()`, `receive_json()`,
  `send()`, `send_json()`, `close(code=1000, reason="")`, async iteration, `closed`, and request
  attributes (`path`, `path_params`, `query`, `headers`, `cookies`, `client`, `request_id`,
  `state`, `user`, `session`).
- **FR-003**: Handshake checks per the clarifications; 426 for non-upgrade requests.
- **FR-004**: Close semantics, limits, heartbeat and shutdown per the clarifications; close codes
  1000, 1001, 1008, 1009, 1011.
- **FR-005**: `StreamingResponse(content, status=200, headers=None, media_type=None)` with an
  async iterable (or sync iterable) of `bytes`/`str`; pull-based backpressure; disconnect and
  shutdown close the iterator.
- **FR-006**: `EventStream(events, *, ping=15, status=200, headers=None)` and
  `ServerSentEvent(data, *, event=None, id=None, retry=None, comment=None)`.
- **FR-007**: `ferro.broadcast`: `publish`, `subscribe` (async context manager and iterator),
  backends `memory` and `database` (PostgreSQL), setting `broadcast`.
- **FR-008**: Metrics: handshakes counted as requests (status 101); gauges for open sockets and
  streams. Access log line per handshake and per stream (written when the headers are sent;
  see Implementation Notes).
- **FR-009**: `TestClient.websocket_connect()` and `TestClient.stream()`.
- **FR-010**: Documentation: a real-time guide (chat example with broadcast, SSE notifications,
  streaming download), deployment notes (proxy settings for WebSockets and SSE).

## Success Criteria *(mandatory)*

- **SC-001**: An echo socket round-trips 1,000 messages in order; 200 sockets open at once on one
  process all receive a broadcast message within 1 s.
- **SC-002**: SSE events arrive individually (each within 100 ms of being yielded).
- **SC-003**: Closing the client ends the server-side handler or generator within 1 s.
- **SC-004**: With PostgreSQL and `--processes 2`, a chat message reaches sockets on both
  processes.
- **SC-005**: HTTP request performance is unchanged (no measurable cost when no socket or stream
  is used).

## Assumptions

- WebSocket compression (permessage-deflate), subprotocol negotiation beyond echoing none, and
  WebSockets over HTTP/2 (RFC 8441) are out of scope.
- Redis or other brokers for broadcast are out of scope.
