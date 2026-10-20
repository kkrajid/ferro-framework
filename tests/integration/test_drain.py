"""Feature 011 US2 — drain before a graceful stop; shutdown summary; second signal."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from tests.support.postgres import free_port

APP = """import asyncio
import os

from ferro import App

app = App()


@app.get("/pid")
async def pid():
    return {"pid": os.getpid()}


@app.get("/slow")
async def slow():
    await asyncio.sleep(1)
    return {"done": True}
"""


def start(tmp_path: Path, *args: str):
    (tmp_path / "app.py").write_text(APP)
    port = free_port()
    environment = {k: v for k, v in os.environ.items() if not k.startswith("FERRO_")}
    log = (tmp_path / "stderr.log").open("wb")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [sys.executable, "-m", "ferro", "run", "--port", str(port), *args],
        cwd=tmp_path,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=log,
        creationflags=flags,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError((tmp_path / "stderr.log").read_text())
        try:
            if httpx.get(f"{base}/pid", timeout=1).status_code == 200:
                return process, base, log
        except httpx.HTTPError:
            time.sleep(0.1)
    process.kill()
    raise AssertionError("server did not start")


def interrupt(process: subprocess.Popen) -> None:
    if sys.platform == "win32":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(signal.SIGTERM)


def test_drain_keeps_serving_with_failing_readiness_then_stops(tmp_path):
    process, base, log = start(tmp_path, "--shutdown-drain", "2", "--shutdown-timeout", "5")
    try:
        signalled = time.monotonic()
        interrupt(process)
        # The drain starts when the signal is handled; slow CI machines need more than a moment.
        deadline = signalled + 1.5
        while True:
            readiness = httpx.get(f"{base}/readyz", timeout=5)
            if readiness.status_code == 503 or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        assert readiness.status_code == 503
        assert readiness.json() == {"status": "not ready", "failing": ["server"]}
        assert httpx.get(f"{base}/healthz", timeout=5).status_code == 200
        # A request started during the drain is served normally.
        assert httpx.get(f"{base}/slow", timeout=5).json() == {"done": True}
        code = process.wait(2 + 5 + 1 + 2)
        took = time.monotonic() - signalled
    finally:
        if process.poll() is None:
            process.kill()
        log.close()
    err = (tmp_path / "stderr.log").read_text()
    assert code == 0, err
    assert took >= 2.0, "the drain lasts the configured time"
    assert "draining for 2 s" in err
    assert "stopped: 0 requests in flight at stop, 0 finished, 0 cut off" in err


def test_requests_in_flight_at_the_stop_finish_and_are_counted(tmp_path):
    process, base, log = start(tmp_path, "--shutdown-timeout", "5")
    try:
        results = []
        thread = threading.Thread(
            target=lambda: results.append(httpx.get(f"{base}/slow", timeout=10).status_code)
        )
        thread.start()
        time.sleep(0.3)
        interrupt(process)
        thread.join()
        code = process.wait(10)
    finally:
        if process.poll() is None:
            process.kill()
        log.close()
    err = (tmp_path / "stderr.log").read_text()
    assert results == [200]
    assert code == 0, err
    assert "stopped: 1 requests in flight at stop, 1 finished, 0 cut off" in err


def test_second_signal_during_the_drain_stops_at_once(tmp_path):
    process, base, log = start(tmp_path, "--shutdown-drain", "10")
    try:
        interrupt(process)
        time.sleep(0.5)
        assert httpx.get(f"{base}/readyz", timeout=5).status_code == 503
        second = time.monotonic()
        interrupt(process)
        code = process.wait(10)
        took = time.monotonic() - second
    finally:
        if process.poll() is None:
            process.kill()
        log.close()
    assert code == 1
    assert took < 5, "no waiting for the rest of the drain"


@pytest.mark.skipif(sys.platform != "win32", reason="validated on the Windows development machine")
def test_every_worker_drains(tmp_path):
    process, base, log = start(tmp_path, "--processes", "2", "--shutdown-drain", "4")
    try:
        deadline = time.monotonic() + 30
        while len({httpx.get(f"{base}/pid", timeout=5).json()["pid"] for _ in range(20)}) < 2:
            assert time.monotonic() < deadline, "both workers serve"
        signalled = time.monotonic()
        interrupt(process)
        time.sleep(0.5)
        answers = {httpx.get(f"{base}/readyz", timeout=5).status_code for _ in range(10)}
        assert answers == {503}, "every worker reports not ready during the drain"
        code = process.wait(4 + 30 + 10)
        took = time.monotonic() - signalled
    finally:
        if process.poll() is None:
            process.kill()
        log.close()
    err = (tmp_path / "stderr.log").read_text()
    assert code == 0, err
    assert took >= 4.0
    assert err.count("draining for 4 s") == 2
