"""US3 / SC-003: predictable behaviour under stress and failure, against real servers."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from tests.stress.conftest import size
from tests.support.process import rss_bytes

APP = """
import asyncio
import os

from ferro import App, db, models
from ferro.db import transaction


class Item(models.Model):
    name = models.String(max_length=40)

    class Meta:
        table = "stress_item"


app = App(database_url=os.environ.get("STRESS_DATABASE_URL"))
state = {"cancelled": 0}


@app.get("/pid")
async def pid():
    return {"pid": os.getpid()}


@app.post("/setup")
async def setup():
    await db.create_tables([Item])
    return {"ok": True}


@app.get("/plain")
async def plain():
    return {"ok": True}


@app.get("/fail")
async def fail():
    raise RuntimeError("deliberate failure")


@app.get("/read")
async def read():
    return {"n": await Item.objects.count()}


@app.post("/write")
async def write():
    await Item.objects.create(name="w")
    return {"ok": True}


@app.post("/seed/{n}")
async def seed(n: int):
    await Item.objects.bulk_create([Item(name=f"item {i}") for i in range(n)])
    return {"n": n}


@app.get("/all")
async def all_rows():
    return {"n": len(await Item.objects.all())}


@app.post("/pending")
async def pending():
    try:
        async with transaction():
            await Item.objects.create(name="pending")
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        state["cancelled"] += 1
        raise
    return {"ok": True}


@app.get("/state")
async def get_state():
    pending = await Item.objects.filter(name="pending").count()
    return {"cancelled": state["cancelled"], "pending": pending}


@app.post("/upload")
async def upload(request):
    return {"size": len(await request.body())}
"""


WATCHER = """
import json, sys, time
import httpx
latencies = []
with httpx.Client(base_url=sys.argv[1], timeout=10) as http:
    for _ in range(20):
        started = time.monotonic()
        http.get("/plain")
        latencies.append(time.monotonic() - started)
        time.sleep(0.05)
print(json.dumps(latencies))
"""


def sqlite_url(directory: Path, suffix: str = "") -> tuple[str, Path]:
    path = directory / "stress.db"
    return f"sqlite:///{path.as_posix()}{suffix}", path


def start_app(serve, tmp_path: Path, *args: str, url: str | None = None) -> object:
    env = {"STRESS_DATABASE_URL": url} if url else {}
    server = serve(APP, *args, env=env)
    if url:
        assert httpx.post(f"{server.base}/setup", timeout=30).status_code == 200
    return server


def server_pid(server) -> int:
    return httpx.get(f"{server.base}/pid").json()["pid"]


def test_mixed_concurrency_answers_correctly_without_growing(serve, tmp_path, stress_results):
    url, _ = sqlite_url(tmp_path)
    server = start_app(serve, tmp_path, "--log-level", "warning", url=url)
    pid = server_pid(server)
    paths = ["/plain", "/read", "/fail"]
    expected = {"/plain": 200, "/read": 200, "/fail": 500}

    local = threading.local()

    def burst(count: int) -> list[tuple[str, int, bool]]:
        def one(i: int) -> tuple[str, int, bool]:
            path = paths[i % 3]
            if not hasattr(local, "http"):  # keep-alive per client thread, like real clients
                local.http = httpx.Client(base_url=server.base, timeout=30)
            response = local.http.get(path)
            envelope = path != "/fail" or response.json()["error"]["status"] == 500
            return path, response.status_code, envelope

        with ThreadPoolExecutor(32) as pool:
            return list(pool.map(one, range(count)))

    burst(size(600, 3000))  # warm-up
    warm = rss_bytes(pid)
    results = burst(size(3000, 30000))
    after = rss_bytes(pid)
    wrong = [r for r in results if r[1] != expected[r[0]] or not r[2]]
    growth = after / warm - 1
    stress_results.record(
        "Mixed concurrency (plain, database, failing routes), 32 clients",
        f"{len(results):,} requests, {len(wrong)} wrong answers; memory {warm / 2**20:.1f} → "
        f"{after / 2**20:.1f} MiB ({growth:+.1%})",
        not wrong and growth <= 0.10,
    )
    assert not wrong, wrong[:5]
    assert growth <= 0.10, f"memory grew {growth:.1%}"


def test_more_connections_than_the_limit_wait_their_turn(serve, tmp_path, stress_results):
    server = start_app(serve, tmp_path, "--workers", "2", "--max-connections", "4")
    clients = size(40, 200)
    failures = []

    def client(_: int) -> None:
        try:
            with httpx.Client(base_url=server.base, timeout=60) as http:
                for _ in range(5):
                    assert http.get("/plain").status_code == 200
        except Exception as exc:
            failures.append(exc)

    started = time.monotonic()
    with ThreadPoolExecutor(clients) as pool:
        list(pool.map(client, range(clients)))
    took = time.monotonic() - started
    probe = time.monotonic()
    assert httpx.get(f"{server.base}/plain", timeout=5).status_code == 200
    after = time.monotonic() - probe
    stress_results.record(
        "More connections than max_connections (4 allowed)",
        f"{clients} clients x 5 requests in {took:.1f} s, {len(failures)} failed; "
        f"next request {after * 1000:.0f} ms",
        not failures and after < 1,
    )
    assert not failures, failures[:3]
    assert after < 1


def test_slow_sqlite_answers_the_timeout_response_on_time(serve, tmp_path, stress_results):
    url, path = sqlite_url(tmp_path)
    server = start_app(serve, tmp_path, "--request-timeout", "1", url=url)
    locker = sqlite3.connect(path, isolation_level=None)
    locker.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        response = httpx.post(f"{server.base}/write", timeout=10)
        took = time.monotonic() - started
    finally:
        locker.execute("ROLLBACK")
        locker.close()
    time.sleep(5)  # the abandoned write waits out the busy timeout, then frees its connection
    later = [httpx.post(f"{server.base}/write", timeout=10).status_code for _ in range(8)]
    stress_results.record(
        "Slow database (SQLite write lock) vs 1 s request timeout",
        f"{response.status_code} after {took:.2f} s; then {later.count(200)}/8 writes succeed",
        response.status_code == 503 and took < 2 and later == [200] * 8,
    )
    assert response.status_code == 503 and response.headers["retry-after"] == "1"
    assert took < 2
    assert later == [200] * 8, "every connection is free again"


def test_slow_postgresql_answers_the_timeout_response_on_time(
    serve, tmp_path, stress_postgres, stress_results
):
    url = f"{stress_postgres.url}?sslmode=disable"
    server = start_app(serve, tmp_path, "--request-timeout", "1", url=url)
    holder = subprocess.Popen(
        [
            stress_postgres.psql,
            "-d",
            stress_postgres.url,
            "-c",
            "BEGIN; LOCK TABLE stress_item IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(5); COMMIT;",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    granted = (
        "SELECT count(*) FROM pg_locks WHERE mode = 'AccessExclusiveLock' AND granted "
        "AND relation = 'stress_item'::regclass"
    )
    deadline = time.monotonic() + 20
    while stress_postgres.query(granted) != "1":
        assert time.monotonic() < deadline, "the lock was never taken"
        time.sleep(0.05)
    started = time.monotonic()
    response = httpx.get(f"{server.base}/read", timeout=10)
    took = time.monotonic() - started
    holder.wait(30)
    time.sleep(0.5)
    later = [httpx.get(f"{server.base}/read", timeout=10).status_code for _ in range(12)]
    stress_results.record(
        "Slow database (PostgreSQL table lock) vs 1 s request timeout",
        f"{response.status_code} after {took:.2f} s; then {later.count(200)}/12 reads succeed",
        response.status_code == 503 and took < 2 and later == [200] * 12,
    )
    assert response.status_code == 503
    assert took < 2
    assert later == [200] * 12


def test_postgresql_outage_under_load(serve, tmp_path, stress_postgres, stress_results):
    url = f"{stress_postgres.url}?sslmode=disable"
    server = start_app(serve, tmp_path, "--log-level", "error", url=url)
    statuses: dict[str, list[int]] = {"/read": [], "/plain": []}
    stop = threading.Event()

    def load(path: str) -> None:
        with httpx.Client(base_url=server.base, timeout=30) as http:
            while not stop.is_set():
                statuses[path].append(http.get(path).status_code)

    threads = [threading.Thread(target=load, args=(p,)) for p in ("/read", "/plain") * 4]
    for thread in threads:
        thread.start()
    time.sleep(1)
    stress_postgres.stop()
    time.sleep(size(2, 5))
    stress_postgres.start()
    time.sleep(3)
    stop.set()
    for thread in threads:
        thread.join(30)
    read, plain = statuses["/read"], statuses["/plain"]
    recovered = httpx.get(f"{server.base}/read", timeout=10).status_code
    stress_results.record(
        "PostgreSQL stopped and restarted under load",
        f"database route: {read.count(200):,} x 200, {read.count(503):,} x 503, "
        f"{len(read) - read.count(200) - read.count(503)} other; plain route: "
        f"{plain.count(200):,} x 200 of {len(plain):,}; after restart {recovered}",
        set(read) <= {200, 503} and 503 in read and set(plain) == {200} and recovered == 200,
    )
    assert set(read) <= {200, 503}, "never an internal error"
    assert 503 in read
    assert set(plain) == {200}
    assert recovered == 200


def test_a_full_database_queue_fails_fast(serve, tmp_path, stress_results):
    url, path = sqlite_url(tmp_path, "?queue_size=8")
    server = start_app(serve, tmp_path, "--log-level", "error", url=url)
    locker = sqlite3.connect(path, isolation_level=None)
    locker.execute("BEGIN IMMEDIATE")
    flood = size(60, 300)
    plain_latency: list[float] = []

    def write(_: int) -> tuple[int, float]:
        started = time.monotonic()
        status = httpx.post(f"{server.base}/write", timeout=60).status_code
        return status, time.monotonic() - started

    # The watcher runs in its own process: in this one it would compete with the flood's client
    # threads for the interpreter and measure the client instead of the server.
    watcher = subprocess.Popen(
        [sys.executable, "-c", WATCHER, server.base],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        with ThreadPoolExecutor(min(flood, 50)) as pool:
            results = list(pool.map(write, range(flood)))
        plain_latency.extend(json.loads(watcher.communicate(timeout=60)[0]))
    finally:
        locker.execute("ROLLBACK")
        locker.close()
    # "Fast" is relative to the 5 s a blocked write would wait; shared CI runners with 50 client
    # threads on 2-3 cores need more headroom than a workstation.
    fast = 3.0 if os.environ.get("CI") else 1.0
    fast_refusals = [took for status, took in results if status == 503 and took < fast]
    p99 = statistics.quantiles(plain_latency, n=100)[98] if len(plain_latency) > 1 else 0
    stress_results.record(
        "Database queue full (queue_size=8, writes blocked)",
        f"{flood} writes: {len(fast_refusals)} refused with 503 in < {fast:g} s; plain route p99 "
        f"{p99 * 1000:.0f} ms during the flood",
        len(fast_refusals) >= flood // 2 and p99 < 0.5,
    )
    assert len(fast_refusals) >= flood // 2
    assert p99 < 0.5, "requests that do not use the database stay fast"


def test_bodies_over_the_limit_are_refused_quickly(serve, tmp_path, stress_results):
    server = start_app(serve, tmp_path, "--max-body-size", "1024")
    at_limit = httpx.post(f"{server.base}/upload", content=b"x" * 1024, timeout=10)
    over = httpx.post(f"{server.base}/upload", content=b"x" * 1025, timeout=10)
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as raw:
        started = time.monotonic()
        raw.sendall(b"POST /upload HTTP/1.1\r\nHost: t\r\nContent-Length: 104857600\r\n\r\n")
        reply = raw.recv(256)
        declared_took = time.monotonic() - started
    passed = (
        at_limit.status_code == 200
        and over.status_code == 413
        and b" 413 " in reply
        and declared_took < 1
    )
    stress_results.record(
        "Bodies at/over the 1 KiB limit; 100 MiB declared",
        f"at limit {at_limit.status_code}, over {over.status_code}, declared 100 MiB answered "
        f"in {declared_took * 1000:.0f} ms without the body",
        passed,
    )
    assert at_limit.json() == {"size": 1024}
    assert over.status_code == 413
    assert b" 413 " in reply and declared_took < 1


def test_slow_bodies_get_request_timeout(serve, tmp_path, stress_results):
    server = start_app(serve, tmp_path, "--request-timeout", "1")
    with socket.create_connection(("127.0.0.1", server.port), timeout=10) as raw:
        started = time.monotonic()
        raw.sendall(b"POST /upload HTTP/1.1\r\nHost: t\r\nContent-Length: 100\r\n\r\nabc")
        reply = raw.recv(256)
        took = time.monotonic() - started
        raw.settimeout(5)
        try:
            while raw.recv(4096):
                pass
            closed = True  # the server closed the connection after answering
        except (TimeoutError, ConnectionResetError) as exc:
            closed = isinstance(exc, ConnectionResetError)
    stress_results.record(
        "Body trickled slower than the 1 s request timeout",
        f"{reply.split(b'\r\n', 1)[0].decode()} after {took:.2f} s, connection closed: {closed}",
        b" 408 " in reply and took < 2 and closed,
    )
    assert b" 408 " in reply
    assert took < 2
    assert closed


def test_large_result_sets_do_not_grow_memory(serve, tmp_path, stress_results):
    url, _ = sqlite_url(tmp_path)
    server = start_app(serve, tmp_path, url=url)
    rows = size(20_000, 100_000)
    assert httpx.post(f"{server.base}/seed/{rows}", timeout=120).json() == {"n": rows}
    pid = server_pid(server)
    counts, memory = [], []
    for _ in range(8):
        counts.append(httpx.get(f"{server.base}/all", timeout=120).json()["n"])
        memory.append(rss_bytes(pid))
    # The first reads grow the allocator's pools; a leak would keep growing after that.
    growth = max(memory[2:]) / memory[1] - 1
    stress_results.record(
        f"Large result set ({rows:,} rows) read 8 times",
        f"memory after each read: {', '.join(f'{m / 2**20:.0f}' for m in memory)} MiB "
        f"(at most {growth:+.1%} over the second read)",
        counts == [rows] * 8 and growth <= 0.10,
    )
    assert counts == [rows] * 8
    assert growth <= 0.10, f"memory kept growing: {growth:.1%}"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_client_disconnect_rolls_back_and_frees_the_connection(
    backend, serve, tmp_path, stress_results, request
):
    if backend == "sqlite":
        url, _ = sqlite_url(tmp_path)
    else:
        postgres = request.getfixturevalue("stress_postgres")
        schema = f"disconnect_{int(time.time() * 1000)}"
        postgres.execute(f'CREATE SCHEMA "{schema}"')
        url = postgres.schema_url(schema)
    server = start_app(serve, tmp_path, url=url)
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as raw:
        raw.sendall(b"POST /pending HTTP/1.1\r\nHost: t\r\nContent-Length: 0\r\n\r\n")
        time.sleep(0.5)
    deadline = time.monotonic() + 10
    state = {}
    while time.monotonic() < deadline:
        state = httpx.get(f"{server.base}/state", timeout=10).json()
        if state["cancelled"]:
            break
        time.sleep(0.1)
    writes = [httpx.post(f"{server.base}/write", timeout=10).status_code for _ in range(4)]
    stress_results.record(
        f"Client disconnects during a transaction ({backend})",
        f"handler cancelled: {bool(state['cancelled'])}, uncommitted rows: {state['pending']}, "
        f"{writes.count(200)}/4 writes afterwards",
        state["cancelled"] == 1 and state["pending"] == 0 and writes == [200] * 4,
    )
    assert state["cancelled"] == 1, "the handler was cancelled"
    assert state["pending"] == 0, "the transaction was rolled back"
    assert writes == [200] * 4


def test_slow_headers_do_not_hold_up_other_clients(serve, tmp_path, stress_results):
    server = start_app(serve, tmp_path, "--header-timeout", "1")
    with socket.create_connection(("127.0.0.1", server.port), timeout=10) as raw:
        started = time.monotonic()
        raw.sendall(b"GET /plain HTTP/1.1\r\n")
        normal: list[int] = []
        normal_took = 0.0

        def others() -> None:
            nonlocal normal_took
            normal.extend(
                httpx.get(f"{server.base}/plain", timeout=5).status_code for _ in range(10)
            )
            normal_took = time.monotonic() - started

        # Other clients run meanwhile; the cut-off is measured on its own (on slow machines ten
        # requests alone can take longer than the limit).
        other_clients = threading.Thread(target=others)
        other_clients.start()
        try:
            data = raw.recv(256)
            while data and b"\r\n" not in data:
                data += raw.recv(256)
        except (ConnectionResetError, ConnectionAbortedError):
            data = b""
        cut_off = time.monotonic() - started
        other_clients.join(60)
    passed = normal == [200] * 10 and cut_off < 3 and (data == b"" or b" 408 " in data)
    stress_results.record(
        "A client sending headers slowly (1 s header timeout)",
        f"other clients: {normal.count(200)}/10 OK in {normal_took:.2f} s; slow client cut off "
        f"after {cut_off:.2f} s ({data.split(b'\r\n', 1)[0].decode() or 'connection closed'})",
        passed,
    )
    assert normal == [200] * 10
    assert cut_off < 3
    assert data == b"" or b" 408 " in data
