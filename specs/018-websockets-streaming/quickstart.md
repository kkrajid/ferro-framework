# Quickstart: WebSockets, Streaming Responses and Server-Sent Events

Validates the feature end to end. The user guide is
[docs/guide/16-realtime.md](../../docs/guide/16-realtime.md).

## 1. Echo socket

```python
# app.py
from ferro import App

app = App()


@app.websocket("/echo")
async def echo(ws):
    async for message in ws:
        await ws.send(message)
```

```bash
ferro dev
```

In a browser console on `http://127.0.0.1:8000/`:

```javascript
const ws = new WebSocket("ws://127.0.0.1:8000/echo");
ws.onmessage = (e) => console.log(e.data);
ws.onopen = () => ws.send("hi");          // logs "hi"
```

`curl -i http://127.0.0.1:8000/echo` answers `426 Upgrade Required`.

## 2. Server-sent events

```python
import asyncio

from ferro import EventStream, ServerSentEvent


@app.get("/ticks")
async def ticks():
    async def events():
        for n in range(3):
            yield ServerSentEvent({"n": n}, event="tick", id=n)
            await asyncio.sleep(1)

    return EventStream(events())
```

```bash
curl -N http://127.0.0.1:8000/ticks      # one event per second, not all at the end
```

## 3. Broadcast across processes (PostgreSQL)

```bash
FERRO_DATABASE_URL=postgresql://... FERRO_BROADCAST=database ferro run --processes 2
```

Open several sockets subscribed to one channel (they land on both processes) and publish from
an HTTP route: every socket receives the message. Automated:
`tests/integration/test_realtime_processes.py`.

## 4. Tests

```bash
cargo test -p ferro-http --test realtime
.venv/Scripts/python -m pytest tests/python/test_realtime.py tests/python/test_broadcast.py tests/integration/test_realtime_processes.py tests/integration/test_tls.py
```

Expected: all pass (PostgreSQL tests skip without a server or `FERRO_TEST_POSTGRES_URL`).
