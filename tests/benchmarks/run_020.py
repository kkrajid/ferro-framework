"""Feature 020: the e-commerce checkout (`POST /api/orders`: row lock, stock update, order lines in
one transaction) under load on PostgreSQL, plus the per-query cost of the ORM path.

Runs `examples/ecommerce` with `ferro run`, logs a customer in and drives `oha` at the checkout
for a fixed time; reports p50/p99 latency and requests per second. The interpreter given with
`--python` decides which build of Ferro is measured (a virtual environment with a release wheel,
or a development one built by `maturin develop`), so builds can be compared on one machine:

    .venv/Scripts/python tests/benchmarks/run_020.py --python path/to/venv/python --label release

Requires `oha` and PostgreSQL binaries (or FERRO_TEST_POSTGRES_URL).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.support.postgres import free_port, temporary_cluster  # noqa: E402

SEED = """
from decimal import Decimal
from models import Category, Product
category = await Category.objects.create(name="Bench", slug="bench")
await Product.objects.create(category=category, name="Widget", slug="widget",
                             price=Decimal("9.99"), stock=10**9)
"""

QUERY_PROBE = """
import statistics, time
from models import Product
samples = []
for _ in range(500):
    started = time.perf_counter()
    await Product.objects.filter(pk=1).first()
    samples.append(time.perf_counter() - started)
print("QUERY_P50_MS", statistics.median(samples[50:]) * 1000)
"""


def ferro(python: str, *args: str, cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        [python, "-m", "ferro", *args], cwd=cwd, env=env, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise SystemExit(f"ferro {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout


def stop_tree(process: subprocess.Popen) -> None:
    """Stops the server and its children (on Windows a venv's python.exe is a launcher whose
    child interpreter would otherwise keep running)."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def oha(url: str, cookie: str, duration: str, connections: int) -> dict:
    body = json.dumps({"items": [{"product_id": 1, "quantity": 1}]})
    command = [
        "oha", "-z", duration, "-c", str(connections), "--no-tui", "--output-format", "json",
        "-m", "POST", "-H", "content-type: application/json", "-H", f"cookie: {cookie}",
        "-d", body, url,
    ]  # fmt: skip
    return json.loads(subprocess.run(command, capture_output=True, text=True, check=True).stdout)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default=sys.executable, help="interpreter with Ferro installed")
    parser.add_argument("--label", default="ferro")
    parser.add_argument(
        "--example", type=Path, default=ROOT / "examples" / "ecommerce", help="shop to serve"
    )
    parser.add_argument("--duration", default="10s")
    parser.add_argument("--connections", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    with temporary_cluster() as server, tempfile.TemporaryDirectory() as tmp:
        if server is None:
            raise SystemExit("PostgreSQL binaries not found (or set FERRO_TEST_POSTGRES_URL)")
        project = Path(tmp) / "shop"
        shutil.copytree(args.example, project, ignore=shutil.ignore_patterns("tests"))
        env = {k: v for k, v in os.environ.items() if not k.startswith("FERRO_")}
        env.update(FERRO_DATABASE_URL=server.url, PYTHONDONTWRITEBYTECODE="1")
        ferro(args.python, "migrate", cwd=project, env=env)
        ferro(args.python, "shell", "-c", SEED, cwd=project, env=env)
        probe = ferro(args.python, "shell", "-c", QUERY_PROBE, cwd=project, env=env)
        query_p50 = float(probe.split("QUERY_P50_MS", 1)[1].split()[0])

        port = free_port()
        process = subprocess.Popen(
            [args.python, "-m", "ferro", "run", "--port", str(port),
             "--workers", str(args.workers), "--log-level", "error"],
            cwd=project, env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            start_new_session=sys.platform != "win32",
        )  # fmt: skip
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 60
            while True:
                try:
                    if httpx.get(f"{base}/readyz", timeout=2).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise SystemExit("server did not start")
                time.sleep(0.2)
            with httpx.Client(base_url=base, timeout=30) as client:
                account = {"username": "bench", "password": "bench-password-123"}
                client.post("/api/register", json=account).raise_for_status()
                client.post("/api/login", json=account).raise_for_status()
                # `ferro run` marks the session cookie Secure, so it is sent by hand over HTTP.
                cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
                order = {"items": [{"product_id": 1, "quantity": 1}]}
                for _ in range(50):  # warm-up
                    client.post("/api/orders", json=order, headers={"cookie": cookie})
                check = client.post("/api/orders", json=order, headers={"cookie": cookie})
                if check.status_code != 201:
                    raise SystemExit(f"checkout failed: {check.status_code} {check.text}")
            result = oha(f"{base}/api/orders", cookie, args.duration, args.connections)
        finally:
            stop_tree(process)

    statuses = result.get("statusCodeDistribution", {})
    latency = result["latencyPercentiles"]
    print(
        f"{args.label}: checkout p50 {latency['p50'] * 1000:.2f} ms, "
        f"p99 {latency['p99'] * 1000:.2f} ms, "
        f"{result['summary']['requestsPerSec']:.0f} req/s, statuses {statuses}; "
        f"one ORM query p50 {query_p50:.3f} ms"
    )


if __name__ == "__main__":
    main()
