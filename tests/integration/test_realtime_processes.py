"""Feature 018 SC-004: with PostgreSQL broadcast, a chat message reaches sockets held by every
worker process of `ferro run --processes 2`."""

from __future__ import annotations

import time
import uuid

import httpx
import pytest

from ferro.testing import connect_websocket
from tests.integration.test_processes import interrupt, start, wait_ready
from tests.python.orm.conftest import postgres_server  # noqa: F401 - fixture

APP = """import os

from ferro import App
from ferro.broadcast import broadcast

app = App()


@app.websocket("/chat")
async def chat(ws):
    async with broadcast.subscribe("chat") as messages:
        await ws.send_json({"pid": os.getpid()})
        async for message in messages:
            await ws.send_json(message)


@app.post("/say")
async def say(request):
    data = await request.json()
    await broadcast.publish("chat", {"text": data["text"], "from": os.getpid()})
"""


def test_chat_reaches_every_process(tmp_path, postgres_server):  # noqa: F811
    if postgres_server is None:
        pytest.skip("PostgreSQL not available (install it or set FERRO_TEST_POSTGRES_URL)")
    schema = f"t_{uuid.uuid4().hex[:12]}"
    postgres_server.execute(f'CREATE SCHEMA "{schema}"')
    process, base = start(
        tmp_path,
        "--processes",
        "2",
        source=APP,
        env={
            "FERRO_DATABASE_URL": postgres_server.schema_url(schema),
            "FERRO_BROADCAST": "database",
        },
    )
    sockets = {}
    try:
        wait_ready(base, process, path="/healthz")
        port = int(base.rsplit(":", 1)[1])
        deadline = time.monotonic() + 30
        while len(sockets) < 2 and time.monotonic() < deadline:
            ws = connect_websocket(("127.0.0.1", port), "/chat")
            pid = ws.receive_json()["pid"]
            if pid in sockets:
                ws.close()
            else:
                sockets[pid] = ws
        assert len(sockets) == 2, "sockets on both workers"
        time.sleep(1)  # both listeners have said LISTEN
        response = httpx.post(f"{base}/say", json={"text": "hello"}, timeout=10)
        assert response.status_code == 204
        for ws in sockets.values():
            message = ws.receive_json(timeout=5)
            assert message["text"] == "hello"
    finally:
        for ws in sockets.values():
            ws.close()
        interrupt(process)
        _, err = process.communicate(timeout=60)
    assert process.returncode == 0, err
