"""US2 / SC-002: a stop signal under continuous load, with a drain period."""

from __future__ import annotations

import threading
import time

import httpx

from tests.stress.conftest import size

APP = """
import asyncio
import os

from ferro import App

app = App()


@app.get("/pid")
async def pid():
    return {"pid": os.getpid()}


@app.get("/work")
async def work():
    await asyncio.sleep(0.05)
    return {"ok": True}
"""

DRAIN = 2.0
SHUTDOWN_TIMEOUT = 5.0


def test_drain_under_load_drops_nothing(serve, stress_results):
    server = serve(APP, "--shutdown-drain", str(DRAIN), "--shutdown-timeout", str(SHUTDOWN_TIMEOUT))
    clients = 100  # as SC-002 defines it; the full run only loads longer
    outcomes: list[tuple[float, bool]] = []
    errors: list[tuple[float, str]] = []
    lock = threading.Lock()
    stop_clients = threading.Event()

    def client() -> None:
        with httpx.Client(base_url=server.base, timeout=10) as http:
            while not stop_clients.is_set():
                started = time.monotonic()
                try:
                    ok = http.get("/work").status_code == 200
                except httpx.HTTPError as exc:
                    ok = False
                    with lock:
                        errors.append((started, repr(exc)))
                with lock:
                    outcomes.append((started, ok))
                if not ok:
                    time.sleep(0.05)

    threads = [threading.Thread(target=client, daemon=True) for _ in range(clients)]
    for thread in threads:
        thread.start()
    time.sleep(size(2, 10))
    signalled = time.monotonic()
    server.signal_stop()
    code = server.wait(DRAIN + SHUTDOWN_TIMEOUT + 10)
    exited = time.monotonic() - signalled
    stop_clients.set()
    for thread in threads:
        thread.join(15)

    drain_end = signalled + DRAIN
    before = [ok for started, ok in outcomes if started < drain_end - 0.2]
    during_drain = [ok for started, ok in outcomes if signalled <= started < drain_end - 0.2]
    failed_before = before.count(False)
    passed = (
        code == 0
        and failed_before == 0
        and exited <= DRAIN + SHUTDOWN_TIMEOUT + 1
        and len(during_drain) > 0
    )
    stress_results.record(
        "Stop signal under load, 2 s drain (SC-002)",
        f"{clients} clients; {len(before):,} requests started before the drain ended, "
        f"{failed_before} failed ({len(during_drain):,} of them during the drain); "
        f"exit code {code} after {exited:.2f} s",
        passed,
    )
    log = server.log()
    assert code == 0, log
    late = [
        (round(started - drain_end, 3), error)
        for started, error in errors
        if started < drain_end - 0.2
    ]
    assert failed_before == 0, f"failures (start relative to the drain's end, error): {late[:10]}"
    assert during_drain, "requests kept being served during the drain"
    assert exited <= DRAIN + SHUTDOWN_TIMEOUT + 1
    assert "stopped:" in log
