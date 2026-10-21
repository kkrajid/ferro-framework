"""Feature 010 US1 — `ferro run --processes N`."""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

APP = """import os

from ferro import App

if os.environ.get("FERRO_WORKER_ID") and os.environ.get("BREAK_WORKERS"):
    raise RuntimeError("this worker cannot start")

app = App()


@app.get("/pid")
async def pid():
    return {"pid": os.getpid()}


@app.get("/burn")
def burn():
    total = 0
    for i in range(200_000):
        total += i * i
    return {"pid": os.getpid()}
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start(tmp_path: Path, *args: str, env: dict[str, str] | None = None, source: str = APP):
    (tmp_path / "app.py").write_text(source)
    port = free_port()
    environment = {k: v for k, v in os.environ.items() if not k.startswith("FERRO_")}
    environment.update(env or {})
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    process = subprocess.Popen(
        # Warnings only: stderr is a pipe read at the end; access logs would fill it.
        [
            sys.executable,
            "-m",
            "ferro",
            "run",
            "--port",
            str(port),
            "--log-level",
            "warning",
            *args,
        ],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=flags,
    )
    return process, f"http://127.0.0.1:{port}"


def wait_ready(
    base: str, process: subprocess.Popen, timeout: float = 30, path: str = "/pid"
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"server exited: {process.communicate()}")
        try:
            if httpx.get(f"{base}{path}", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise AssertionError("server did not start")


def interrupt(process: subprocess.Popen) -> None:
    if sys.platform == "win32":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(signal.SIGTERM)


def pids(base: str, count: int = 300) -> set[int]:
    """Process ids answering `count` CPU-bound requests from 32 threads sharing one client (the
    form that was stable on every runner). A client-side read error (Windows reported WinError
    10038 once, with no server error) is retried once; timeouts are not."""
    with ThreadPoolExecutor(32) as pool, httpx.Client(timeout=10) as client:

        def get(_: int) -> int:
            try:
                return client.get(f"{base}/burn").json()["pid"]
            except httpx.ReadError:
                return client.get(f"{base}/burn").json()["pid"]

        return set(pool.map(get, range(count)))


def alive(pid: int) -> bool:
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_workers_share_the_port_restart_and_stop(tmp_path):
    process, base = start(tmp_path, "--processes", "3")
    try:
        wait_ready(base, process)
        seen = pids(base)
        assert len(seen) >= 2, f"requests were served by {seen}"
        victim = next(iter(seen))
        os.kill(victim, signal.SIGTERM)
        deadline = time.monotonic() + 30
        failures = 0
        replaced = False
        while time.monotonic() < deadline and not replaced:
            try:
                served = pids(base, 60)
            except httpx.HTTPError:
                failures += 1
                continue
            replaced = bool(served - seen)
        assert replaced, "a replacement worker serves requests"
        assert failures <= 1
    finally:
        interrupt(process)
        _, err = process.communicate(timeout=60)
    assert process.returncode == 0, err
    assert "3 processes" in err
    time.sleep(1)
    assert not any(alive(pid) for pid in seen), "workers stop with the supervisor"


def test_port_in_use_and_development_mode(tmp_path):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        (tmp_path / "app.py").write_text(APP)
        result = subprocess.run(
            [sys.executable, "-m", "ferro", "run", "--port", str(port), "--processes", "2"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert result.returncode == 3, result.stderr
    assert result.stderr.count("error:") == 1
    dev = subprocess.run(
        [sys.executable, "-m", "ferro", "dev", "--no-reload", "--processes", "2"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert dev.returncode == 2 and "single process" in dev.stderr


def test_workers_failing_at_startup_stop_the_supervisor(tmp_path):
    process, _ = start(tmp_path, "--processes", "2", env={"BREAK_WORKERS": "1"})
    try:
        _, err = process.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        process.kill()
        pytest.fail("the supervisor kept restarting failing workers")
    assert process.returncode == 1
    assert "failed to start" in err


DATABASE_APP = """import faulthandler
import os
import signal

from ferro import App, models

# The test sends SIGUSR1 to the workers when a request hangs: every thread's stack goes to
# stderr (macOS runners have hung here; the stacks show where).
if hasattr(signal, "SIGUSR1"):
    faulthandler.register(signal.SIGUSR1, all_threads=True)


class Visit(models.Model):
    path = models.String(max_length=50)

    class Meta:
        table = "visit"


# Two processes write one SQLite file: on a slow runner a writer can wait longer than the
# default 5 s busy timeout (a POST got an error instead of a pid once on macOS).
app = App(database_url="sqlite:///visits.db?timeout=60")


@app.post("/visits")
async def add():
    await Visit.objects.create(path="/")
    return {"pid": os.getpid()}


@app.get("/visits")
async def count():
    return {"pid": os.getpid(), "count": await Visit.objects.count()}
"""


def _dump_worker_stacks(process: subprocess.Popen) -> None:
    """Asks the supervisor's workers to print their threads' stacks (POSIX)."""
    if sys.platform == "win32":
        return
    children = subprocess.run(
        ["pgrep", "-P", str(process.pid)], capture_output=True, text=True
    ).stdout.split()
    for child in children:
        with contextlib.suppress(ProcessLookupError, ValueError):
            os.kill(int(child), signal.SIGUSR1)
    time.sleep(2)


def test_every_worker_uses_the_same_database(tmp_path):
    (tmp_path / "app.py").write_text(DATABASE_APP)
    environment = {k: v for k, v in os.environ.items() if not k.startswith("FERRO_")}
    subprocess.run(
        [sys.executable, "-m", "ferro", "db", "create-tables"],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
    )
    process, base = start(tmp_path, "--processes", "2", source=DATABASE_APP)
    try:
        wait_ready(base, process, path="/visits")

        # One worker answering does not mean the other is ready: a connection the kernel hands
        # to a worker still importing the app waits for it (a 10 s read timed out once on a
        # macOS runner). The timeouts cover a slow start.
        # A new connection per request: a kept-alive connection stays with one worker.
        def visit(_: int) -> int | str:
            try:
                response = httpx.post(f"{base}/visits", timeout=90)
            except httpx.TimeoutException:
                return "timeout"
            if response.status_code != 200:
                return f"{response.status_code} {response.text}"
            return response.json()["pid"]

        with ThreadPoolExecutor(8) as pool:
            outcomes = list(pool.map(visit, range(40)))
            problems = [o for o in outcomes if isinstance(o, str)]
            if problems:
                _dump_worker_stacks(process)
                interrupt(process)
                _, err = process.communicate(timeout=60)
                raise AssertionError(
                    f"{len(problems)} of 40 writes failed: {problems[:5]}; pids "
                    f"{sorted({o for o in outcomes if isinstance(o, int)})}\n"
                    f"server stderr (tail):\n{err[-12000:]}"
                )
            writers = set(outcomes)
            readers = list(
                pool.map(lambda _: httpx.get(f"{base}/visits", timeout=60).json(), range(40))
            )
            # The kernel decides which worker accepts a connection (macOS can favour one for
            # a while): keep reading until both have answered.
            for _ in range(10):
                if len(writers | {r["pid"] for r in readers}) >= 2:
                    break
                readers += pool.map(
                    lambda _: httpx.get(f"{base}/visits", timeout=60).json(), range(30)
                )
    finally:
        interrupt(process)
        _, err = process.communicate(timeout=60)
    assert process.returncode == 0, err
    assert len(writers | {r["pid"] for r in readers}) >= 2, "both workers served requests"
    assert {r["count"] for r in readers} == {40}, "every worker sees every write"


def test_workers_exit_when_the_supervisor_dies(tmp_path):
    process, base = start(tmp_path, "--processes", "2")
    try:
        wait_ready(base, process)
        seen = pids(base, 100)
    finally:
        process.kill()
        process.communicate(timeout=60)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and any(alive(pid) for pid in seen):
        time.sleep(0.5)
    assert not any(alive(pid) for pid in seen), "orphaned workers stop on their own"
