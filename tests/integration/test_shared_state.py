"""Feature 015: rate limits and metrics shared by the workers of `ferro run --processes N`."""

from __future__ import annotations

import os
import re
import signal
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from tests.integration.test_processes import interrupt, start, wait_ready

APP = """import os

from ferro import App

app = App()


@app.get("/pid")
async def pid():
    return {"pid": os.getpid()}


@app.get("/limited", rate_limit="10/minute")
async def limited():
    return {"pid": os.getpid()}
"""

PROCESSES = "4"


def fresh(url: str) -> httpx.Response:
    """One request on its own connection, so the operating system picks the worker."""
    return httpx.get(url, timeout=10, headers={"connection": "close"})


def pids(base: str, count: int) -> set[int]:
    with ThreadPoolExecutor(16) as pool:
        return {r.json()["pid"] for r in pool.map(lambda _: fresh(f"{base}/pid"), range(count))}


def test_rate_limits_hold_across_processes(tmp_path):
    process, base = start(tmp_path, "--processes", PROCESSES, source=APP)
    try:
        wait_ready(base, process)
        assert len(pids(base, 200)) >= 2, "requests reach several workers"
        with ThreadPoolExecutor(8) as pool:
            responses = list(pool.map(lambda _: fresh(f"{base}/limited"), range(40)))
        codes = [r.status_code for r in responses]
        assert codes.count(200) == 10, codes
        assert codes.count(429) == 30, codes
        refused = next(r for r in responses if r.status_code == 429)
        assert int(refused.headers["retry-after"]) >= 1
    finally:
        interrupt(process)
        _, err = process.communicate(timeout=60)
    assert process.returncode == 0, err


def total(base: str, route: str) -> int:
    text = fresh(f"{base}/metrics").text
    pattern = rf'^ferro_http_requests_total\{{route="{re.escape(route)}",[^}}]*\}} (\d+)$'
    return sum(int(count) for count in re.findall(pattern, text, re.MULTILINE))


def test_metrics_cover_every_process(tmp_path):
    process, base = start(
        tmp_path, "--processes", PROCESSES, source=APP, env={"FERRO_METRICS": "1"}
    )
    try:
        wait_ready(base, process)
        time.sleep(2)  # the warm-up requests' worker has published its count
        before = total(base, "/pid")
        seen = pids(base, 400)
        assert len(seen) >= 2
        time.sleep(2)  # every worker has published since
        totals = [total(base, "/pid") for _ in range(8)]
        assert all(t == before + 400 for t in totals), (before, totals)

        # A worker that dies keeps its counts in the totals.
        os.kill(next(iter(seen)), signal.SIGTERM)
        time.sleep(1)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                after = total(base, "/pid")
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        assert after >= before + 400
    finally:
        interrupt(process)
        _, err = process.communicate(timeout=60)
    assert process.returncode == 0, err
