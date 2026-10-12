"""Measurements for Feature 002 success criteria SC-004 and SC-005.

Requires a release build (``maturin develop --release --uv``) and `oha`. Run from the repo root:

    .venv/Scripts/python tests/benchmarks/run_002.py

Writes tests/benchmarks/results/002-orm-foundation.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "products_api"
RESULTS = Path(__file__).resolve().parent / "results" / "002-orm-foundation.md"
sys.path.insert(0, str(EXAMPLE))

from ferro import db, models  # noqa: E402


class BenchRow(models.Model):
    name = models.String(max_length=100)
    price = models.Decimal(max_digits=10, decimal_places=2)
    stock = models.Integer()
    active = models.Boolean()
    created = models.DateTime()
    note = models.Text()


async def read_and_bulk(directory: Path) -> tuple[float, float]:
    await db.connect(f"sqlite:///{(directory / 'bench.db').as_posix()}")
    try:
        await db.create_tables([BenchRow])
        now = datetime.now(UTC)
        rows = [
            BenchRow(
                name=f"row {i}",
                price=Decimal(i) / 100,
                stock=i,
                active=i % 2 == 0,
                created=now,
                note="x" * 40,
            )
            for i in range(10_000)
        ]
        started = time.perf_counter()
        await BenchRow.objects.bulk_create(rows)
        bulk_seconds = time.perf_counter() - started

        query = BenchRow.objects.order_by("pk")[:1000]
        await query  # warm-up
        timings = []
        for _ in range(20):
            started = time.perf_counter()
            result = await query
            timings.append(time.perf_counter() - started)
            assert len(result) == 1000
        return statistics.median(timings) * 1000, bulk_seconds
    finally:
        await db.disconnect()


def oha(url: str, duration: str, connections: int) -> dict:
    output = subprocess.run(
        ["oha", "-z", duration, "-c", str(connections), "--no-tui", "--output-format", "json", url],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(output)


def failures(report: dict) -> int:
    non_ok = sum(
        c for s, c in report.get("statusCodeDistribution", {}).items() if not s.startswith("2")
    )
    errors = sum(
        c for k, c in report.get("errorDistribution", {}).items() if k != "aborted due to deadline"
    )
    return non_ok + errors


def server_run(directory: Path, port: int) -> tuple[int, float, float]:
    url = f"sqlite:///{(directory / 'api.db').as_posix()}"
    env = {**os.environ, "FERRO_DATABASE_URL": url}
    subprocess.run(
        [sys.executable, "-m", "ferro", "migrate"],
        cwd=EXAMPLE,
        env=env,
        check=True,
        capture_output=True,
    )
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ferro",
            "run",
            "--port",
            str(port),
            "--workers",
            "4",
            "--log-level",
            "error",
        ],
        cwd=EXAMPLE,
        env=env,
        creationflags=flags,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                httpx.get(f"{base}/products", timeout=1)
                break
            except httpx.HTTPError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        with httpx.Client(base_url=base) as client:
            for i in range(20):
                client.post("/products", json={"name": f"p{i}", "price": f"{i}.50", "stock": i})
        listing = subprocess.Popen(
            [
                "oha",
                "-z",
                "15s",
                "-c",
                "100",
                "--no-tui",
                "--output-format",
                "json",
                f"{base}/products",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        time.sleep(2)
        detail = oha(f"{base}/products/1", "10s", 10)
        list_report = json.loads(listing.communicate(timeout=60)[0])
        return (
            failures(list_report) + failures(detail),
            list_report["summary"]["requestsPerSec"],
            detail["latencyPercentiles"]["p99"] * 1000,
        )
    finally:
        if sys.platform == "win32":
            server.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            server.terminate()
        server.wait(60)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        read_ms, bulk_s = asyncio.run(read_and_bulk(directory))
        failed, list_rps, p99_ms = server_run(directory, 8099)

    rows = [
        ("SC-004", "read 1,000 rows (6 fields), median of 20", f"{read_ms:.1f} ms", read_ms < 50),
        ("SC-004", "bulk_create 10,000 rows", f"{bulk_s:.3f} s", bulk_s < 1),
        (
            "SC-005",
            "100 clients on list endpoint (15 s) + 10 clients on a detail route",
            f"{failed} failed; list {list_rps:,.0f} req/s; detail p99 {p99_ms:.1f} ms",
            failed == 0 and p99_ms < 50,
        ),
    ]
    report = "\n".join(
        [
            "# Feature 002 measurements",
            "",
            f"- Date: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
            f"- Machine: {platform.platform()}, {os.cpu_count()} logical CPUs",
            f"- Python {platform.python_version()}, release build, SQLite file database (WAL)",
            "",
            "| Criterion | Scenario | Result | Verdict |",
            "|---|---|---|---|",
            *[f"| {a} | {b} | {c} | {'PASS' if ok else 'FAIL'} |" for a, b, c, ok in rows],
            "",
        ]
    )
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(report, encoding="utf-8")
    sys.stdout.buffer.write(report.encode("utf-8") + b"\n")
    return 0 if all(ok for *_, ok in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
