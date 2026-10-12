"""US1 / SC-001: readiness follows a PostgreSQL outage and recovery; liveness never fails."""

from __future__ import annotations

import time

import httpx

from tests.stress.conftest import size

APP = """
import os

from ferro import App

app = App(database_url=os.environ["STRESS_DATABASE_URL"])


@app.get("/pid")
async def pid():
    return {"pid": os.getpid()}
"""


def poll_until(url: str, expected: dict, live_url: str, limit: float) -> tuple[float, int]:
    """Seconds until ``url`` answers ``expected``; counts liveness failures meanwhile."""
    started = time.monotonic()
    live_failures = 0
    while time.monotonic() - started < limit + 5:
        if httpx.get(url, timeout=5).json() == expected:
            return time.monotonic() - started, live_failures
        if httpx.get(live_url, timeout=5).status_code != 200:
            live_failures += 1
        time.sleep(0.05)
    raise AssertionError(f"{url} never answered {expected}")


def test_readiness_follows_a_database_outage(serve, stress_postgres, stress_results):
    server = serve(APP, env={"STRESS_DATABASE_URL": f"{stress_postgres.url}?sslmode=disable"})
    ready, live = f"{server.base}/readyz", f"{server.base}/healthz"
    assert httpx.get(ready).json() == {"status": "ready"}
    outages, recoveries, live_failures = [], [], 0
    for _ in range(size(2, 5)):
        stress_postgres.stop()
        try:
            took, failures = poll_until(
                ready, {"status": "not ready", "failing": ["database"]}, live, 2.0
            )
            outages.append(took)
            live_failures += failures
        finally:
            stress_postgres.start()
        took, failures = poll_until(ready, {"status": "ready"}, live, 2.0)
        recoveries.append(took)
        live_failures += failures
    worst = max(outages + recoveries)
    stress_results.record(
        "Readiness during PostgreSQL outages (SC-001)",
        f"{len(outages)} outages: reported after {', '.join(f'{t:.2f}' for t in outages)} s; "
        f"recovery after {', '.join(f'{t:.2f}' for t in recoveries)} s; "
        f"{live_failures} liveness failures",
        worst <= 2.0 and live_failures == 0,
    )
    assert worst <= 2.0, (outages, recoveries)
    assert live_failures == 0
