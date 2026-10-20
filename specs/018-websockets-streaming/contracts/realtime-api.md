# Contract: Real-time API (Python)

Public names added in this feature, pinned by `tests/python/test_public_api.py` and listed in
[docs/stability.md](../../../docs/stability.md).

## `ferro.App.websocket`

```python
App.websocket(path: str, *, name: str | None = None, rate_limit: str | None = None,
              csrf: bool = True) -> Callable[[H], H]
```

- Handler: `async def handler(ws, **params)`. The connection parameter is named `ws` or
  `websocket`, or annotated `WebSocket`; other parameters are path/query parameters with the same
  conversion as HTTP routes. Invalid values: the socket is accepted and closed with 1008, the
  reason naming the problem.
- Handshake: `GET` with `Upgrade: websocket`; the engine applies the rate limit (app-wide or
  `rate_limit`) and the cross-origin check (`csrf=False` exempts). Results: 101, 403, 429, or 426
  (`upgrade: websocket`) for a request without `Upgrade`.
- Middleware does not run for WebSocket routes; with `app.add_auth()` `ws.user`/`ws.session` are
  loaded from the session cookie before the handler runs.
- `ferro routes` lists the route with method `GET` (the handshake).

## `ferro.WebSocket` (`ferro.websockets.WebSocket`)

| Member | Contract |
|---|---|
| `await receive() -> str \| bytes` | next message in order; `WebSocketDisconnect(code, reason)` once closed |
| `await receive_text()`, `receive_bytes()` | `TypeError` for the other kind |
| `await receive_json()` | `json.loads` of the next message (`ValueError` if not JSON) |
| `await send(data: str \| bytes)` | queued; waits only while ≥ 1 MiB is queued; `WebSocketDisconnect` after close; `TypeError` for other types |
| `await send_json(value)` | Ferro's JSON encoding, text frame |
| `await close(code=1000, reason="")` | codes 1000, 1001, 1003, 1007, 1008, 1011, 3000–4999 (`ValueError` otherwise); reason ≤ 123 bytes; idempotent |
| `async for message in ws` | `receive()` until disconnect, then the loop ends |
| `closed`, `close_code`, `close_reason` | `close_*` are `None` while open |
| `path`, `raw_path`, `query`, `query_string`, `path_params`, `headers`, `cookies`, `client`, `request_id` | from the handshake request |
| `user`, `session`, `state` | `user` is `None` without `add_auth()`; `session` read-only |

Server behaviour: handler return → 1000; `WebSocketDisconnect` escaping the handler → normal end;
other exception → 1011 + error log with request id; client close → handler cancelled unless it
is waiting in `receive()` (which raises `WebSocketDisconnect`) or the application closed first;
inbound message > `max_body_size` → 1009; protocol error → 1002; no frame for 60 s (pings every
20 s) → dropped (1006); server stop → 1001.

## `ferro.WebSocketDisconnect`

`WebSocketDisconnect(code: int = 1000, reason: str = "")`, attributes `code`, `reason`.

## `ferro.StreamingResponse`

```python
StreamingResponse(content: AsyncIterable | Iterable, status: int = 200,
                  headers=None, media_type: str | None = None)
```

- Chunks are `str` (UTF-8) or bytes-like; another type ends the stream and is logged.
- The engine requests chunk *n+1* only after chunk *n* was handed to the connection.
- Response middleware sees status and headers (body is not readable there).
- Client disconnect / server stop: a chunk in progress is cancelled, then `aclose()` runs the
  generator's `finally`; nothing is logged for this normal case.
- An exception from the generator, or a server stop before the end, aborts the body
  (HTTP/1.1: the connection closes without the final chunk; HTTP/2: the stream is reset);
  the exception is logged with the request id.

## `ferro.ServerSentEvent` and `ferro.EventStream`

```python
ServerSentEvent(data=None, *, event=None, id=None, retry=None, comment=None)
EventStream(events: AsyncIterable | Iterable, *, ping: float | None = 15.0,
            status: int = 200, headers=None)
```

- Encoding: `: comment`, `event:`, `id:`, `retry:`, then one `data:` line per line of data
  (`\r\n`, `\r`, `\n` all split), terminated by a blank line. `str` data as is; any other value
  JSON-encoded. `ValueError` for line breaks in `event`/`id`/`comment` or a negative/non-int
  `retry`.
- A yielded value that is not a `ServerSentEvent` becomes `ServerSentEvent(value)`.
- Headers: `content-type: text/event-stream`, `cache-control: no-cache`,
  `x-accel-buffering: no` (unless given in `headers`).
- `: ping` after `ping` seconds without an event.

## `ferro.broadcast`

```python
broadcast: Broadcast
await broadcast.publish(channel: str, message: JSONValue) -> None
async with broadcast.subscribe(channel: str) as subscription: ...   # Subscription
await subscription.get(timeout: float | None = None)   # TimeoutError after timeout
async for message in subscription: ...
subscription.dropped: int
broadcast.subscribers(channel: str) -> int
configure(backend: str = "memory") -> Broadcast        # "memory" | "database"
MAX_MESSAGE_BYTES = 7000
```

- Channels are non-empty strings (`ValueError`); messages JSON-compatible (`TypeError`); each
  subscriber receives its own copy, in publish order (per publisher).
- `database`: PostgreSQL only; encoded size > `MAX_MESSAGE_BYTES` → `ValueError` naming the
  limit; inside a transaction delivered at commit.
- Setting `broadcast` applies at server start unless `configure()` was called.

## `ferro.testing`

| Name | Contract |
|---|---|
| `TestClient.websocket_connect(path, *, headers=None) -> TestWebSocket` | with the client's cookies; `WebSocketRejected` (`.response`: `TestResponse`) for a non-101 answer |
| `TestClient.stream(method, path, *, json=None, data=None, headers=None)` | context manager → `TestStreamResponse` (`status_code`, `headers`, `iter_bytes()`, `iter_lines()`, `iter_events()`) |
| `connect_websocket(address, path, *, headers=None) -> TestWebSocket` | same client against any running server |
| `TestWebSocket` | `send`, `send_json`, `receive(timeout=10)`, `receive_text`, `receive_bytes`, `receive_json`, `close(code=1000, reason="")`, `close_code`, `close_reason`; context manager |
| `WebSocketDisconnect`, `WebSocketRejected` | re-exported for tests |
