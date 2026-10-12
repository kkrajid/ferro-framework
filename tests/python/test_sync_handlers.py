"""User Story 6 — synchronous handlers without stalling the server (FR-006, SC-004)."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from ferro import App, HTTPException, Response


def make_app() -> App:
    app = App(sync_threads=16)

    @app.get("/sync-sleep")
    def sync_sleep():
        time.sleep(1)
        return {"slept": True}

    @app.get("/sync-kinds/{kind}")
    def kinds(kind: str):
        values = {
            "json": {"a": 1},
            "text": "t",
            "bytes": b"b",
            "none": None,
            "response": Response("r", status=202),
        }
        return values[kind]

    @app.get("/sync-params/{id}")
    def params(id: int, request, q: str = "x"):
        return {
            "id": id,
            "q": q,
            "path": request.path,
            "thread": threading.current_thread().name,
        }

    @app.get("/sync-error")
    def error():
        raise HTTPException(409, "conflict")

    @app.get("/sync-no-args")
    def no_args():
        return {"ok": True}

    @app.get("/fast")
    async def fast():
        return {"fast": True}

    return app


@pytest.fixture
def client(serve):
    return serve(make_app(), workers=4)


@pytest.mark.parametrize(
    ("kind", "status", "content"),
    [
        ("json", 200, b'{"a":1}'),
        ("text", 200, b"t"),
        ("bytes", 200, b"b"),
        ("none", 204, b""),
        ("response", 202, b"r"),
    ],
)
def test_sync_handler_response_kinds(client, kind, status, content):
    response = client.get(f"/sync-kinds/{kind}")
    assert response.status_code == status
    assert response.content == content


def test_sync_handler_params_run_on_executor(client):
    body = client.get("/sync-params/5?q=y").json()
    assert body["id"] == 5
    assert body["q"] == "y"
    assert body["path"] == "/sync-params/5"
    assert body["thread"].startswith("ferro-sync")


def test_sync_handler_without_args(client):
    assert client.get("/sync-no-args").json() == {"ok": True}


def test_sync_handler_errors_use_envelope(client):
    response = client.get("/sync-error")
    assert response.status_code == 409
    assert response.json()["error"]["message"] == "conflict"


def test_blocking_sync_handlers_do_not_stall_async_routes(client):
    latencies: list[float] = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=10) as pool:
        sleepers = [pool.submit(client.get, "/sync-sleep") for _ in range(10)]
        time.sleep(0.1)
        while not all(s.done() for s in sleepers):
            t0 = time.monotonic()
            assert client.get("/fast").status_code == 200
            latencies.append(time.monotonic() - t0)
        statuses = [s.result().status_code for s in sleepers]
    elapsed = time.monotonic() - started
    assert statuses == [200] * 10
    assert elapsed < 3, "sync handlers should run in parallel on the executor"
    latencies.sort()
    p99 = latencies[max(0, int(len(latencies) * 0.99) - 1)]
    assert len(latencies) >= 10
    assert p99 < 0.05, f"p99 {p99 * 1000:.1f} ms"
