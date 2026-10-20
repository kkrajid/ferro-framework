# Real-time: WebSockets, server-sent events and streaming

Ferro has three tools for pushing data to clients as it happens:

| Tool | Direction | Use for |
|---|---|---|
| **WebSocket** (`@app.websocket`) | both ways, messages | chat, multiplayer, collaborative editing, live dashboards with input |
| **Server-sent events** (`EventStream`) | server → browser, over plain HTTP | notifications, progress bars, live feeds, logs |
| **Streaming response** (`StreamingResponse`) | server → client, one response | large exports and downloads, generated files |

and a **broadcast** hub to send one message to many connections — across processes and machines
with PostgreSQL.

## WebSockets

```python
from ferro import App

app = App()


@app.websocket("/echo")
async def echo(ws):
    async for message in ws:          # str (text frame) or bytes (binary frame)
        await ws.send(message)
    # the loop ends when the client disconnects
```

Browser side:

```javascript
const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/echo`);
ws.onmessage = (event) => console.log(event.data);
ws.onopen = () => ws.send("hello");
```

### The `ws` object

| | |
|---|---|
| `await ws.receive()` | next message, `str` or `bytes`; raises `WebSocketDisconnect` once closed |
| `await ws.receive_text()`, `receive_bytes()`, `receive_json()` | typed variants |
| `async for message in ws` | messages until the client disconnects |
| `await ws.send(text_or_bytes)`, `await ws.send_json(value)` | send a message |
| `await ws.close(code=1000, reason="")` | close (codes 1000, 1001, 1003, 1007, 1008, 1011 or your own 3000–4999) |
| `ws.closed`, `ws.close_code`, `ws.close_reason` | state |
| `ws.path`, `ws.path_params`, `ws.query`, `ws.headers`, `ws.cookies`, `ws.client`, `ws.request_id` | the handshake request |
| `ws.user`, `ws.session` | with `app.add_auth()`: the logged-in user (anonymous otherwise) and the session (read-only) |
| `ws.state` | your own values |

Path and query parameters work as for HTTP routes:

```python
@app.websocket("/rooms/{room}")
async def room(ws, room: int, name: str = "guest"): ...
```

Invalid parameters close the connection with code 1008 and the problem as the reason.

### What happens when

| Event | Effect |
|---|---|
| handler returns | connection closed normally (1000) |
| handler raises | closed with 1011; the error and traceback are logged with the request id |
| client disconnects while the handler waits in `receive()` | `receive()` raises `WebSocketDisconnect(code, reason)` (`async for` just ends) |
| client disconnects while the handler waits on something else | the handler is cancelled (its `finally` blocks run) |
| `send()` after the connection closed | raises `WebSocketDisconnect` |
| a message larger than `max_body_size` (1 MiB) | closed with 1009 |
| client silent for 60 s (it does not answer pings, sent every 20 s) | connection dropped |
| server stops | closed with 1001 (going away) — clients should reconnect |

Back-pressure is automatic: Ferro stops reading from a client whose messages your handler does
not consume (32 waiting), and `send()` waits only when 1 MiB is already queued for a slow client.

### Security

The handshake goes through the engine's checks before your code runs:

- **Origin check**: a page on another site cannot open a socket to your app (cross-site
  WebSocket hijacking would otherwise let it act with the visitor's cookies). Same-origin pages,
  `trusted_origins` and CORS-listed origins connect; so do non-browser clients (no `Origin`).
  Exempt a route with `@app.websocket(path, csrf=False)` only for token-authenticated APIs.
- **Rate limit**: the app-wide limit, or `@app.websocket(path, rate_limit="10/minute")`.
- **Authentication**: with `app.add_auth()`, check `ws.user.is_authenticated` and close with
  1008 otherwise:

```python
@app.websocket("/private")
async def private(ws):
    if not ws.user.is_authenticated:
        await ws.close(1008, "login required")
        return
    ...
```

A plain HTTP request to a WebSocket address gets `426 Upgrade Required`.

## Server-sent events (SSE)

```python
import asyncio

from ferro import EventStream, ServerSentEvent


@app.get("/progress/{job}")
async def progress(job: int):
    async def events():
        for percent in range(0, 101, 10):
            yield ServerSentEvent({"percent": percent}, event="progress", id=percent)
            await asyncio.sleep(0.5)
        yield ServerSentEvent("done", event="finished")

    return EventStream(events())
```

```javascript
const source = new EventSource("/progress/42");
source.addEventListener("progress", (e) => console.log(JSON.parse(e.data).percent));
source.addEventListener("finished", () => source.close());
```

- Each yielded value is one event: a `ServerSentEvent(data, event=, id=, retry=, comment=)`, a
  `str` (the data), or any JSON value (sent as JSON).
- Multi-line data is split into several `data:` lines as the format requires.
- A `: ping` comment is sent after 15 s without events (`EventStream(events, ping=...)`), so
  proxies and browsers keep the connection open.
- Headers `content-type: text/event-stream`, `cache-control: no-cache` and
  `x-accel-buffering: no` (nginx) are set.
- Browsers reconnect automatically and send `Last-Event-ID`; read it with
  `request.headers.get("last-event-id")` to resume.

## Streaming responses

```python
from ferro import StreamingResponse


@app.get("/export.csv")
async def export():
    async def rows():
        yield "id,name,price\n"
        for product in await Product.objects.order_by("pk"):
            yield f"{product.pk},{product.name},{product.price}\n"

    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={"content-disposition": 'attachment; filename="products.csv"'},
    )
```

- `content` is an async generator (or any iterable) of `str`/`bytes` chunks.
- Chunks are sent as they are produced; the next one is requested only after the client took
  the previous one, so a slow client slows the generator instead of filling memory.
- `request_timeout` covers the handler until it **returns** the response, not the stream.
- If the client disconnects or the server stops, the generator is closed (`finally` runs).
- A body that cannot finish — the generator raises, or the server stops first — is **aborted**:
  over HTTP/1.1 the connection closes without the final chunk, over HTTP/2 the stream is
  reset. Clients therefore see an incomplete response, never a complete-looking truncated
  file. The exception is logged with the request id.
- Generators run on the event loop: avoid blocking calls inside them.

## Broadcast: one message to many connections

```python
from ferro.broadcast import broadcast

await broadcast.publish("room:42", {"user": "ann", "text": "hi"})

async with broadcast.subscribe("room:42") as messages:
    async for message in messages:
        ...
```

| Backend | Reach | Setting |
|---|---|---|
| `memory` (default) | subscribers in the same process | — |
| `database` | every process and machine using the same **PostgreSQL** database (`LISTEN`/`NOTIFY`) | `App(broadcast="database")` or `FERRO_BROADCAST=database` |

- Messages must be JSON-compatible; each subscriber gets its own copy. With the database backend
  a message may be at most 7,000 bytes of JSON (larger: store it and broadcast its id).
- A subscriber that falls behind keeps the newest 1,000 messages; older ones are dropped and
  counted in `subscription.dropped`.
- Published inside a `transaction()`, a database broadcast is delivered when the transaction
  commits.
- `ferro run --processes N` **needs** the database backend for subscribers in other processes to
  see a message; SQLite has no notification mechanism, so with SQLite use one process for
  real-time features.
- The database backend keeps one extra connection per process for `LISTEN` (reconnected with
  back-off if it drops; messages published meanwhile are missed). It must reach PostgreSQL
  directly or through PgBouncer in *session* mode — transaction pooling drops notifications.

### Example: a chat room

```python
import asyncio

from ferro import App
from ferro.broadcast import broadcast

app = App(broadcast="database")      # PostgreSQL: works with --processes and several machines
app.add_auth()


@app.websocket("/chat/{room}")
async def chat(ws, room: str):
    if not ws.user.is_authenticated:
        await ws.close(1008, "login required")
        return
    channel = f"chat:{room}"
    async with broadcast.subscribe(channel) as messages:

        async def forward():                       # room → this client
            async for message in messages:
                await ws.send_json(message)

        forwarding = asyncio.create_task(forward())
        try:
            async for text in ws:                  # this client → room
                await broadcast.publish(channel, {"user": ws.user.username, "text": text[:500]})
        finally:
            forwarding.cancel()
```

### Example: live notifications with SSE

```python
from ferro import EventStream, ServerSentEvent
from ferro.auth import login_required


@app.get("/notifications")
@login_required
async def notifications(request):
    async def events():
        async with broadcast.subscribe(f"user:{request.user.pk}") as messages:
            async for message in messages:
                yield ServerSentEvent(message, event="notification")

    return EventStream(events())


# anywhere else, e.g. after saving an order:
await broadcast.publish(f"user:{order.customer_id}", {"order": order.pk, "status": "shipped"})
```

## Testing

```python
from ferro.testing import TestClient


def test_echo():
    with TestClient(app) as client:
        with client.websocket_connect("/echo") as ws:
            ws.send("hi")
            assert ws.receive() == "hi"


def test_progress_events():
    with TestClient(app) as client:
        with client.stream("GET", "/progress/1") as response:
            events = list(response.iter_events())
        assert events[-1].event == "finished"
```

`websocket_connect(path, headers=...)` sends the client's cookies (log in first for
authenticated sockets) and raises `WebSocketRejected` (with `.response`) when the handshake is
refused. `stream()` gives `iter_bytes()`, `iter_lines()` and `iter_events()` while the response
arrives. `ferro.testing.connect_websocket(("127.0.0.1", 8000), "/echo")` connects to any running
server.

## Deployment

- **Proxies** must pass WebSocket upgrades and not buffer event streams. Caddy passes upgrades
  by itself; add `flush_interval -1` to `reverse_proxy` (as in
  [`deploy/Caddyfile`](../../deploy/Caddyfile)) so streamed responses are not held back by
  `encode`. nginx needs the settings in [`deploy/nginx.conf`](../../deploy/nginx.conf):
  `proxy_http_version 1.1`, the `Upgrade`/`Connection` headers, and a `proxy_read_timeout`
  longer than your quietest connection; event streams turn nginx's buffering off themselves
  with the `x-accel-buffering: no` header (for other streamed responses, add that header or
  `proxy_buffering off`).
- **Load balancers**: allow long-lived connections (idle timeouts above 60 s; Ferro pings every
  20 s). Sticky sessions are not needed when you use the database broadcast backend.
- **Shutdown**: on stop, sockets close with 1001 and streams are cut off, so clients reconnect
  to another instance; give clients reconnect logic (EventSource has it built in).
- **Capacity**: each open socket or stream costs one Python task and small buffers;
  `max_connections` (default 10,000) bounds them. Watch `ferro_websocket_connections` and
  `ferro_http_streams_open` in [metrics](11-logging-monitoring.md).
- WebSockets use HTTP/1.1 upgrades; SSE and streaming responses also work over HTTP/2.

## Limits

No permessage-deflate compression or subprotocol negotiation; no WebSockets over HTTP/2
(RFC 8441); broadcast backends are memory and PostgreSQL only (no Redis yet).
