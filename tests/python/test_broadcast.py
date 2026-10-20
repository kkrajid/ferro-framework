"""Feature 018 US3: broadcast to many subscribers, in one process (memory) and across processes
(PostgreSQL LISTEN/NOTIFY)."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from ferro import App, ConfigurationError, db
from ferro import broadcast as broadcast_module
from ferro.broadcast import MAX_QUEUED, broadcast, configure
from ferro.config import resolve
from ferro.testing import TestClient
from tests.python.orm.conftest import postgres_server  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def memory_backend():
    configure("memory")
    yield
    broadcast._stop()
    broadcast.explicit = False
    broadcast.backend = "memory"


async def test_every_subscriber_gets_every_message_in_order():
    async with (
        broadcast.subscribe("room") as first,
        broadcast.subscribe("room") as second,
        broadcast.subscribe("other") as unrelated,
    ):
        for n in range(5):
            await broadcast.publish("room", {"n": n})
        assert [(await first.get(1))["n"] for _ in range(5)] == [0, 1, 2, 3, 4]
        assert [(await second.get(1))["n"] for _ in range(5)] == [0, 1, 2, 3, 4]
        with pytest.raises(TimeoutError):
            await unrelated.get(0.05)
    assert broadcast.subscribers("room") == 0, "ended subscriptions are forgotten"
    await broadcast.publish("room", "nobody listens")  # no error


async def test_subscribers_get_copies_and_slow_ones_drop_the_oldest():
    async with broadcast.subscribe("c") as a, broadcast.subscribe("c") as b:
        await broadcast.publish("c", {"items": [1]})
        (await a.get(1))["items"].append(2)
        assert await b.get(1) == {"items": [1]}
        for n in range(MAX_QUEUED + 5):
            await broadcast.publish("c", n)
        assert a.dropped == 5
        assert await a.get(1) == 5


async def test_messages_must_be_json():
    with pytest.raises(TypeError, match="JSON"):
        await broadcast.publish("c", object())
    with pytest.raises(ValueError, match="channel"):
        await broadcast.publish("", 1)


def test_two_hundred_sockets_receive_one_message():
    """SC-001: 200 sockets on one process all receive a broadcast within 1 s."""
    app = App(max_connections=1000)

    @app.websocket("/live")
    async def live(ws):
        async with broadcast.subscribe("news") as messages:
            await ws.send("subscribed")
            async for message in messages:
                await ws.send_json(message)

    @app.post("/publish")
    async def publish(request):
        await broadcast.publish("news", await request.json())

    with TestClient(app) as client:
        sockets = [client.websocket_connect("/live") for _ in range(200)]
        try:
            for ws in sockets:
                assert ws.receive() == "subscribed"
            started = time.monotonic()
            assert client.post("/publish", json={"headline": "hi"}).status_code == 204
            received = [ws.receive_json() for ws in sockets]
            elapsed = time.monotonic() - started
        finally:
            for ws in sockets:
                ws.close()
    assert received == [{"headline": "hi"}] * 200
    assert elapsed < 1.0, elapsed


def test_database_broadcast_needs_postgresql():
    with pytest.raises(ConfigurationError, match="PostgreSQL"):
        resolve(env={"FERRO_BROADCAST": "database", "FERRO_DATABASE_URL": "sqlite:///x.db"})
    settings = resolve(
        env={"FERRO_BROADCAST": "database", "FERRO_DATABASE_URL": "postgresql://u:p@h/db"}
    )
    assert settings.broadcast == "database"


@pytest.fixture
def postgres(postgres_server):  # noqa: F811
    if postgres_server is None:
        pytest.skip("PostgreSQL not available (install it or set FERRO_TEST_POSTGRES_URL)")
    schema = f"t_{uuid.uuid4().hex[:12]}"
    postgres_server.execute(f'CREATE SCHEMA "{schema}"')
    url = postgres_server.schema_url(schema)
    db.connect_now(url, pool_size=2)
    yield url
    broadcast._stop()
    db.disconnect_now()


async def test_postgresql_notifications_reach_subscribers(postgres):
    configure("database")
    async with broadcast.subscribe("orders") as messages:
        await asyncio.sleep(0.3)  # the listener connection says LISTEN
        await broadcast.publish("orders", {"id": 1, "note": "é"})
        assert await messages.get(5) == {"id": 1, "note": "é"}
    with pytest.raises(ValueError, match="too large"):
        await broadcast.publish("orders", "x" * 8000)


async def test_postgresql_broadcast_in_a_transaction_waits_for_the_commit(postgres):
    configure("database")
    async with broadcast.subscribe("orders") as messages:
        await asyncio.sleep(0.3)
        async with db.transaction():
            await broadcast.publish("orders", "committed")
            with pytest.raises(TimeoutError):
                await messages.get(0.3)
        assert await messages.get(5) == "committed"
        with pytest.raises(RuntimeError):
            async with db.transaction():
                await broadcast.publish("orders", "rolled back")
                raise RuntimeError
        with pytest.raises(TimeoutError):
            await messages.get(0.3)


def test_module_api():
    assert broadcast_module.MAX_MESSAGE_BYTES == 7000
    with pytest.raises(ValueError, match="unknown broadcast backend"):
        configure("redis")
