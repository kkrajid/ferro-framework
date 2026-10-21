"""Ferro vs FastAPI (uvicorn) vs Flask (waitress) on the same machine (Feature 025).

Needs a venv with the Ferro release wheel plus ``fastapi uvicorn flask waitress pydantic``, and
``oha`` on PATH. Run with that venv's Python from any folder:

    python tests/benchmarks/run_025_compare.py [--seconds 10] [--rounds 3] [--connections 50]

Each scenario runs against each server in turn (rounds interleaved), one process per server,
with the client on the same machine; medians are reported. Writes
tests/benchmarks/results/025-compare.md. Only the processes this script started are stopped.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

RESULTS = Path(__file__).resolve().parent / "results" / "025-compare.md"

FERRO = """
import sqlite3, threading
from pydantic import BaseModel
from ferro import App, models

app = App(database_url="sqlite:///bench.db")


class Item(models.Model):
    name = models.String(max_length=50)
    price = models.Integer(default=0)

    class Meta:
        table = "item"


class ItemIn(BaseModel):
    name: str
    price: int
    tags: list[str] = []


@app.get("/hello")
async def hello():
    return {"message": "hello"}


@app.get("/items/{item_id}")
async def item(item_id: int, q: str = ""):
    return {"item_id": item_id, "q": q}


@app.post("/items")
async def create(data: ItemIn):
    return {"name": data.name, "price": data.price, "tags": data.tags}


@app.get("/db/{item_id}")
async def db_read(item_id: int):
    found = await Item.objects.get(pk=item_id)
    return {"id": found.pk, "name": found.name, "price": found.price}
"""

FASTAPI = """
import sqlite3, threading
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()
local = threading.local()


def connection():
    if not hasattr(local, "db"):
        local.db = sqlite3.connect("bench.db", check_same_thread=False)
    return local.db


class ItemIn(BaseModel):
    name: str
    price: int
    tags: list[str] = []


@app.get("/hello")
async def hello():
    return {"message": "hello"}


@app.get("/items/{item_id}")
async def item(item_id: int, q: str = ""):
    return {"item_id": item_id, "q": q}


@app.post("/items")
async def create(data: ItemIn):
    return {"name": data.name, "price": data.price, "tags": data.tags}


@app.get("/db/{item_id}")
def db_read(item_id: int):  # sync: FastAPI runs it in its thread pool
    row = connection().execute(
        "SELECT id, name, price FROM item WHERE id = ?", (item_id,)
    ).fetchone()
    return {"id": row[0], "name": row[1], "price": row[2]}
"""

FLASK = """
import sqlite3, threading
from flask import Flask, jsonify, request
from pydantic import BaseModel, ValidationError

app = Flask(__name__)
local = threading.local()


def connection():
    if not hasattr(local, "db"):
        local.db = sqlite3.connect("bench.db", check_same_thread=False)
    return local.db


class ItemIn(BaseModel):
    name: str
    price: int
    tags: list[str] = []


@app.get("/hello")
def hello():
    return jsonify(message="hello")


@app.get("/items/<int:item_id>")
def item(item_id):
    return jsonify(item_id=item_id, q=request.args.get("q", ""))


@app.post("/items")
def create():
    try:
        data = ItemIn.model_validate_json(request.get_data())
    except ValidationError as exc:
        return jsonify(detail=exc.errors(include_url=False)), 422
    return jsonify(name=data.name, price=data.price, tags=data.tags)


@app.get("/db/<int:item_id>")
def db_read(item_id):
    row = connection().execute(
        "SELECT id, name, price FROM item WHERE id = ?", (item_id,)
    ).fetchone()
    return jsonify(id=row[0], name=row[1], price=row[2])
"""

SCENARIOS = [
    ("hello world (JSON)", "GET", "/hello", None),
    ("path + query parameters", "GET", "/items/42?q=phones", None),
    (
        "JSON body validated by Pydantic",
        "POST",
        "/items",
        json.dumps({"name": "Phone", "price": 15000, "tags": ["new", "sale"]}),
    ),
    ("SQLite read by primary key", "GET", "/db/7", None),
]


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
    else:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(10)


def prepare_database(folder: Path) -> None:
    db = sqlite3.connect(folder / "bench.db")
    db.execute(
        "CREATE TABLE item (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name VARCHAR(50) NOT NULL, price INTEGER NOT NULL DEFAULT 0)"
    )
    db.executemany(
        "INSERT INTO item (name, price) VALUES (?, ?)",
        [(f"item {i}", i * 10) for i in range(1, 101)],
    )
    db.commit()
    db.close()


def start(name: str, folder: Path, port: int) -> subprocess.Popen:
    python = sys.executable
    commands = {
        "Ferro": [
            python,
            "-m",
            "ferro",
            "run",
            "ferro_app:app",
            "--port",
            str(port),
            "--workers",
            "4",
            "--log-level",
            "error",
        ],
        "FastAPI (uvicorn)": [
            python,
            "-m",
            "uvicorn",
            "fastapi_app:app",
            "--port",
            str(port),
            "--log-level",
            "error",
            "--no-access-log",
        ],
        "Flask (waitress)": [
            python,
            "-m",
            "waitress",
            "--port",
            str(port),
            "--threads",
            "8",
            "flask_app:app",
        ],
    }
    flags = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("FERRO_")}
    process = subprocess.Popen(
        commands[name],
        cwd=folder,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        **flags,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{name} exited: {process.stderr.read().decode()[-2000:]}")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/hello", timeout=1).status_code == 200:
                return process
        except httpx.HTTPError:
            time.sleep(0.2)
    stop_tree(process)
    raise RuntimeError(f"{name} did not start")


def check(port: int) -> None:
    """Every server answers every scenario correctly before it is measured."""
    base = f"http://127.0.0.1:{port}"
    assert httpx.get(f"{base}/items/42?q=phones").json() == {"item_id": 42, "q": "phones"}
    assert httpx.post(
        f"{base}/items", content=SCENARIOS[2][3], headers={"content-type": "application/json"}
    ).json()["tags"] == ["new", "sale"]
    assert (
        httpx.post(
            f"{base}/items", content=b'{"name": 1}', headers={"content-type": "application/json"}
        ).status_code
        == 422
    )
    assert httpx.get(f"{base}/db/7").json() == {"id": 7, "name": "item 7", "price": 70}


def run_oha(
    port: int, method: str, path: str, body: str | None, seconds: int, connections: int
) -> dict:
    command = [
        "oha",
        "--no-tui",
        "--output-format",
        "json",
        "-z",
        f"{seconds}s",
        "-c",
        str(connections),
        "-m",
        method,
        f"http://127.0.0.1:{port}{path}",
    ]
    if body is not None:
        command += ["-d", body, "-H", "content-type: application/json"]
    out = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    codes = data.get("statusCodeDistribution", {})
    ok = sum(v for k, v in codes.items() if k.startswith("2"))
    total = sum(codes.values())
    percentiles = data.get("latencyPercentiles", {})
    return {
        "rps": data["summary"]["requestsPerSec"],
        "p50": (percentiles.get("p50") or 0) * 1000,
        "p99": (percentiles.get("p99") or 0) * 1000,
        "failed": total - ok + sum(data.get("errorDistribution", {}).values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--connections", type=int, default=50)
    args = parser.parse_args()
    import ferro._ferro as native

    if native.BUILD_PROFILE != "release":
        print("warning: Ferro is a debug build; results are not representative")
    servers = ["Ferro", "FastAPI (uvicorn)", "Flask (waitress)"]
    results: dict[tuple[str, str], list[dict]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "ferro_app.py").write_text(FERRO)
        (folder / "fastapi_app.py").write_text(FASTAPI)
        (folder / "flask_app.py").write_text(FLASK)
        prepare_database(folder)
        for round_index in range(args.rounds):
            for name in servers:
                port = free_port()
                process = start(name, folder, port)
                try:
                    check(port)
                    run_oha(port, "GET", "/hello", None, 2, args.connections)  # warm-up
                    for label, method, path, body in SCENARIOS:
                        result = run_oha(port, method, path, body, args.seconds, args.connections)
                        results.setdefault((label, name), []).append(result)
                        print(
                            f"round {round_index + 1} {name:18} {label:32} "
                            f"{result['rps']:9.0f} req/s  p50 {result['p50']:6.2f} ms  "
                            f"p99 {result['p99']:7.2f} ms  failed {result['failed']}",
                            flush=True,
                        )
                finally:
                    stop_tree(process)
    from importlib.metadata import version

    lines = [
        "# Feature 025: Ferro vs FastAPI vs Flask",
        "",
        f"Measured {datetime.now(UTC):%Y-%m-%d %H:%M} UTC on {platform.platform()}, "
        f"{os.cpu_count()} logical CPUs, CPython {platform.python_version()}; oha "
        f"{args.connections} connections, {args.seconds} s per run after a warm-up, "
        f"{args.rounds} rounds (servers alternating); medians. Client and servers share the "
        "machine.",
        "",
        f"Ferro {version('ferro-framework')} ({native.BUILD_PROFILE} build, "
        "`ferro run --workers 4`, 1 process); "
        f"FastAPI {version('fastapi')} on uvicorn {version('uvicorn')} (1 worker); "
        f"Flask {version('flask')} on waitress {version('waitress')} (8 threads). "
        "Database scenario: Ferro's ORM; FastAPI and Flask with `sqlite3` directly (no ORM).",
        "",
        "| Scenario | Server | req/s | p50 ms | p99 ms | failed | vs FastAPI |",
        "|---|---|---|---|---|---|---|",
    ]
    for label, *_ in SCENARIOS:
        base = statistics.median(r["rps"] for r in results[(label, "FastAPI (uvicorn)")])
        for name in servers:
            runs = results[(label, name)]
            rps = statistics.median(r["rps"] for r in runs)
            p50 = statistics.median(r["p50"] for r in runs)
            p99 = statistics.median(r["p99"] for r in runs)
            failed = sum(r["failed"] for r in runs)
            lines.append(
                f"| {label} | {name} | {rps:,.0f} | {p50:.2f} | {p99:.2f} | {failed} "
                f"| {rps / base:.2f}x |"
            )
    RESULTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
