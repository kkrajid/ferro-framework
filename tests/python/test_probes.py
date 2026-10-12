"""Feature 011 US1 — liveness and readiness probes."""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from ferro import App, ConfigurationError
from ferro.testing import TestClient
from tests.support.postgres import temporary_cluster


def probe_app(**settings) -> App:
    app = App(**settings)

    @app.get("/hello")
    async def hello():
        return {"hello": "world"}

    return app


def base(client: TestClient) -> str:
    return f"http://127.0.0.1:{client._address[1]}"


def test_probes_with_sqlite(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'probe.db').as_posix()}"
    with TestClient(probe_app(), database_url=url, create_tables=[]) as client:
        live = client.get("/healthz")
        assert live.status_code == 200
        assert live.json() == {"status": "ok"}
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready"}
        assert ready.headers["cache-control"] == "no-store"


def test_readiness_without_a_database():
    with TestClient(probe_app()) as client:
        assert client.get("/readyz").json() == {"status": "ready"}


def test_moved_and_disabled_probes():
    with TestClient(probe_app(health_path="", ready_path="/-/ready")) as client:
        assert client.get("/healthz").status_code == 404
        assert client.get("/-/ready").json() == {"status": "ready"}


@pytest.mark.parametrize(
    ("path", "setting"),
    [("/healthz", "health_path"), ("/readyz/", "ready_path"), ("/metrics", "metrics_path")],
)
def test_routes_on_probe_addresses_are_refused(path, setting):
    app = App(metrics=True)

    @app.get(path)
    async def taken():
        return {}

    with pytest.raises(ConfigurationError) as info, TestClient(app):
        pass
    message = str(info.value)
    assert setting in message
    assert "taken" in message


def test_probe_requests_are_not_access_logged(capfd):
    with TestClient(probe_app()) as client:
        client.get("/hello")
        client.get("/healthz")
        client.get("/readyz")
    err = capfd.readouterr().err
    assert "path=/hello" in err
    assert "path=/healthz" not in err
    assert "path=/readyz" not in err


@pytest.fixture(scope="module")
def probe_postgres():
    with temporary_cluster() as server:
        if server is None:
            pytest.skip("PostgreSQL not available")
        server.execute("ALTER SYSTEM SET log_statement = 'all'")
        server.execute("SELECT pg_reload_conf()")
        yield server


# Statements as logged with log_statement=all; prepared ones appear as "execute <name>: ...".
SELECT_ONE = re.compile(r"(?:statement|execute [^:]*): SELECT 1\s*$", re.MULTILINE)


def wait_for(url: str, expected: dict, limit: float) -> float:
    started = time.monotonic()
    while time.monotonic() - started < limit + 3:
        if httpx.get(url, timeout=5).json() == expected:
            return time.monotonic() - started
        time.sleep(0.05)
    raise AssertionError(f"readiness never became {expected}")


def test_readiness_follows_postgresql(probe_postgres):
    server = probe_postgres
    app = probe_app()
    with TestClient(app, database_url=f"{server.url}?sslmode=disable", create_tables=[]) as client:
        ready = f"{base(client)}/readyz"
        assert httpx.get(ready).json() == {"status": "ready"}
        server.stop()
        try:
            took = wait_for(ready, {"status": "not ready", "failing": ["database"]}, 2.0)
            assert took <= 2.0, f"outage reported after {took:.2f} s"
            assert httpx.get(f"{base(client)}/healthz").status_code == 200
        finally:
            server.start()
        took = wait_for(ready, {"status": "ready"}, 2.0)
        assert took <= 2.0, f"recovery reported after {took:.2f} s"

        # A flood of probes runs the database check at most once per second.
        time.sleep(1.2)
        log = Path(server.log)
        before = len(SELECT_ONE.findall(log.read_text(errors="replace")))
        with ThreadPoolExecutor(20) as pool:
            answers = list(pool.map(lambda _: httpx.get(ready).status_code, range(20)))
        assert answers == [200] * 20
        time.sleep(0.3)
        checks = len(SELECT_ONE.findall(log.read_text(errors="replace"))) - before
        assert 1 <= checks <= 2, f"{checks} database checks for 20 probes"
