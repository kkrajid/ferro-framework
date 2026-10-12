"""Stress, failure and shutdown suite (Feature 011): real `ferro run` servers under load.

Sizes are reduced by default (the whole suite stays under a few minutes); ``FERRO_STRESS=full``
runs the full sizes and writes ``tests/benchmarks/results/011-stress.md``.
"""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from tests.support.postgres import PostgresServer, free_port, temporary_cluster

FULL = os.environ.get("FERRO_STRESS") == "full"
RESULTS = Path(__file__).resolve().parents[1] / "benchmarks" / "results" / "011-stress.md"


def size(reduced: int, full: int) -> int:
    """A workload size: ``full`` with FERRO_STRESS=full, otherwise ``reduced``."""
    return full if FULL else reduced


@dataclass
class Served:
    """One `ferro run` subprocess. Its stderr goes to a file, so it can never fill a pipe."""

    process: subprocess.Popen[bytes]
    port: int
    directory: Path
    log_path: Path
    _log: object = field(repr=False, default=None)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def log(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def alive(self) -> bool:
        return self.process.poll() is None

    def signal_stop(self) -> None:
        """The platform's stop signal (Ctrl+Break on Windows, SIGTERM elsewhere)."""
        if sys.platform == "win32":
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            self.process.send_signal(signal.SIGTERM)

    def wait(self, timeout: float) -> int:
        return self.process.wait(timeout)

    def stop(self, timeout: float = 60) -> int:
        if self.alive():
            self.signal_stop()
        try:
            return self.process.wait(timeout)
        finally:
            self.close()

    def kill(self) -> None:
        if self.alive():
            self.process.kill()
            self.process.wait(30)
        self.close()

    def close(self) -> None:
        if self._log is not None:
            self._log.close()  # type: ignore[attr-defined]
            self._log = None


def wait_until_serving(base: str, process: subprocess.Popen[bytes], path: str, log: Path) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"server exited with {process.returncode}:\n{log.read_text()}")
        try:
            if httpx.get(f"{base}{path}", timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise AssertionError(f"server did not start:\n{log.read_text()}")


@pytest.fixture
def serve(tmp_path: Path) -> Iterator[Callable[..., Served]]:
    """Starts ``ferro run`` on an app source in a fresh directory; the app must answer
    ``ready_path`` (default ``/pid``) once it serves. Stops every server at the end."""
    started: list[Served] = []

    def start(
        source: str,
        *args: str,
        env: dict[str, str] | None = None,
        ready_path: str = "/pid",
        name: str = "app",
    ) -> Served:
        directory = tmp_path / f"{name}-{len(started)}"
        directory.mkdir()
        (directory / "app.py").write_text(source, encoding="utf-8")
        port = free_port()
        environment = {k: v for k, v in os.environ.items() if not k.startswith("FERRO_")}
        environment.update(env or {})
        log_path = directory / "stderr.log"
        log = log_path.open("wb")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        process = subprocess.Popen(
            [sys.executable, "-m", "ferro", "run", "--port", str(port), *args],
            cwd=directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
            creationflags=flags,
        )
        served = Served(process, port, directory, log_path, log)
        started.append(served)
        wait_until_serving(served.base, process, ready_path, log_path)
        return served

    yield start
    for served in started:
        served.kill()


@pytest.fixture(scope="session")
def stress_postgres() -> Iterator[PostgresServer]:
    """A PostgreSQL cluster of its own: scenarios stop and restart it."""
    with temporary_cluster() as server:
        if server is None:
            pytest.skip("PostgreSQL not available (install it or set FERRO_TEST_PG_BIN)")
        yield server


@dataclass
class Results:
    rows: list[tuple[str, str, str]] = field(default_factory=list)

    def record(self, scenario: str, numbers: str, passed: bool) -> None:
        self.rows.append((scenario, numbers, "PASS" if passed else "FAIL"))


@pytest.fixture(scope="session")
def stress_results() -> Iterator[Results]:
    results = Results()
    yield results
    if not FULL or not results.rows:
        return
    lines = [
        "# Feature 011 results: stress, failure and shutdown suite",
        "",
        f"Measured {datetime.now(UTC):%Y-%m-%d %H:%M} UTC on {platform.platform()}, "
        f"{os.cpu_count()} logical CPUs, CPython {platform.python_version()}, "
        "`FERRO_STRESS=full pytest tests/stress`, clients on the same machine.",
        "",
        "| Scenario | Result | Verdict |",
        "|---|---|---|",
        *(f"| {scenario} | {numbers} | {verdict} |" for scenario, numbers, verdict in results.rows),
    ]
    RESULTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
