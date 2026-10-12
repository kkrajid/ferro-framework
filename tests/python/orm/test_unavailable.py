"""Feature 011 US3 — database unavailability: one distinct error, answered with 503."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
import uuid

import pytest

from ferro import App, db, models
from ferro.models import DatabaseUnavailableError, OperationalError
from ferro.testing import TestClient


class Gadget(models.Model):
    name = models.String(max_length=20)

    class Meta:
        table = "t11_gadget"


class Ghost(models.Model):
    """Never created: queries fail with a missing table."""

    name = models.String(max_length=20)

    class Meta:
        table = "t11_ghost"


def restartable(server):
    if server is None or not server.can_restart:
        pytest.skip("needs the temporary local PostgreSQL cluster")
    return server


def pg_url(server) -> str:
    schema = f"t_{uuid.uuid4().hex[:12]}"
    server.execute(f'CREATE SCHEMA "{schema}"')
    return server.schema_url(schema)


def test_hierarchy_and_exports():
    assert issubclass(DatabaseUnavailableError, OperationalError)
    assert db.DatabaseUnavailableError is DatabaseUnavailableError


async def test_stopped_postgresql_is_unavailable(postgres_server):
    server = restartable(postgres_server)
    await db.connect(pg_url(server), pool_size=1)
    try:
        await db.create_tables([Gadget])
        await Gadget.objects.create(name="a")
        server.stop()
        try:
            with pytest.raises(DatabaseUnavailableError):
                await Gadget.objects.count()
        finally:
            server.start()
        assert await Gadget.objects.count() == 1, "works again after the restart"
    finally:
        await db.disconnect()


async def test_a_terminated_connection_is_replaced_transparently(postgres_server):
    """A connection the server closed is noticed before the next query, which reconnects."""
    server = restartable(postgres_server)
    await db.connect(pg_url(server), pool_size=1)
    try:
        await db.create_tables([Gadget])
        server.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'ferro' "
            "AND pid <> pg_backend_pid() AND backend_type = 'client backend'"
        )
        assert await Gadget.objects.count() == 0
    finally:
        await db.disconnect()


async def test_sql_errors_are_not_unavailability(database):
    with pytest.raises(OperationalError) as info:
        await Ghost.objects.count()
    assert not isinstance(info.value, DatabaseUnavailableError)


async def test_a_full_sqlite_queue_fails_at_once(tmp_path):
    path = tmp_path / "queue.db"
    await db.connect(f"sqlite:///{path.as_posix()}?queue_size=1", pool_size=1)
    try:
        await db.create_tables([Gadget])
        locker = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        locker.execute("BEGIN IMMEDIATE")
        locker.execute("INSERT INTO t11_gadget (name) VALUES ('held')")
        release = threading.Timer(0.5, locker.execute, ("ROLLBACK",))
        release.start()
        started = time.monotonic()
        results = await asyncio.gather(
            *(Gadget.objects.create(name=str(i)) for i in range(6)), return_exceptions=True
        )
        release.join()
        locker.close()
        errors = [r for r in results if isinstance(r, BaseException)]
        assert errors, "work beyond the queue is refused"
        assert all(isinstance(e, DatabaseUnavailableError) for e in errors), errors
        assert len(errors) < len(results), "queued work still runs"
        assert time.monotonic() - started < 4, "nothing waited for the busy timeout"
    finally:
        await db.disconnect()


def unavailable_app() -> App:
    app = App()

    @app.get("/count")
    async def count():
        return {"count": await Gadget.objects.count()}

    @app.get("/degrade")
    async def degrade():
        try:
            return {"count": await Gadget.objects.count()}
        except DatabaseUnavailableError:
            return {"count": None, "stale": True}

    @app.get("/ghost")
    async def ghost():
        return {"count": await Ghost.objects.count()}

    return app


def test_unhandled_unavailability_is_503(postgres_server, capfd):
    server = restartable(postgres_server)
    with TestClient(
        unavailable_app(), database_url=pg_url(server), create_tables=[Gadget]
    ) as client:
        assert client.get("/count").json() == {"count": 0}
        assert client.get("/ghost").status_code == 500, "a missing table is a bug, not an outage"
        server.stop()
        try:
            response = client.get("/count")
            degraded = client.get("/degrade")
        finally:
            server.start()
        assert client.get("/count").status_code == 200
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    body = response.text
    for leak in ("PostgreSQL", "connect", "127.0.0.1", "ferro@"):
        assert leak not in body, body
    assert degraded.json() == {"count": None, "stale": True}
    err = capfd.readouterr().err
    request_id = response.headers["x-request-id"]
    lines = [line for line in err.splitlines() if request_id in line]
    assert any("database unavailable" in line and "WARN" in line for line in lines), lines
    assert not any("Traceback" in line for line in lines)
