from __future__ import annotations

import asyncio
import inspect
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from ferro.config import resolve
from ferro.server import ServerRunner

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

_BANNER = re.compile(r"on (http://\S+) \(")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    """Runs ``async def`` tests in a fresh event loop (no plugin dependency)."""
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        names = pyfuncitem._fixtureinfo.argnames
        asyncio.run(pyfuncitem.obj(**{name: pyfuncitem.funcargs[name] for name in names}))
        return True
    return None


@pytest.fixture
def serve() -> Iterator[Callable[..., httpx.Client]]:
    """Serves an App in-process on an ephemeral port and returns an HTTP client for it."""
    started: list[tuple[ServerRunner, httpx.Client]] = []

    def _serve(app, **overrides) -> httpx.Client:
        cli = {"host": "127.0.0.1", "port": 0, "workers": 2, **overrides}
        settings = resolve(cli=cli, env={}, app=app._settings, mode="run")
        runner = ServerRunner(app, settings)
        host, port = runner.start()
        client = httpx.Client(base_url=f"http://{host}:{port}", timeout=10)
        started.append((runner, client))
        return client

    yield _serve
    for runner, client in started:
        client.close()
        runner.stop(graceful=True)
        runner.wait(10)
        runner.close()


@dataclass
class FerroProcess:
    """A `ferro` CLI subprocess with its stderr collected line by line."""

    process: subprocess.Popen[str]
    lines: queue.Queue[str] = field(default_factory=queue.Queue)
    seen: list[str] = field(default_factory=list)
    base_url: str = ""

    def wait_for(self, pattern: str, timeout: float = 20) -> re.Match[str]:
        regex = re.compile(pattern)
        for line in self.seen:
            if match := regex.search(line):
                return match
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.1)
            except queue.Empty:
                if self.process.poll() is not None and self.lines.empty():
                    break
                continue
            self.seen.append(line)
            if match := regex.search(line):
                return match
        raise AssertionError(f"no line matching {pattern!r}; stderr so far:\n{self.output}")

    @property
    def output(self) -> str:
        while not self.lines.empty():
            self.seen.append(self.lines.get_nowait())
        return "".join(self.seen)

    def interrupt(self) -> None:
        if sys.platform == "win32":
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            self.process.send_signal(signal.SIGINT)

    def terminate(self) -> None:
        if sys.platform == "win32":
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            self.process.terminate()

    def finish(self, timeout: float = 20) -> int:
        try:
            return self.process.wait(timeout)
        finally:
            self.output  # noqa: B018 - drain collected lines


@pytest.fixture
def ferro_cli() -> Iterator[Callable[..., FerroProcess]]:
    """Starts `python -m ferro ...` in a new process group; kills leftovers after the test."""
    processes: list[FerroProcess] = []

    def _start(*args: str, cwd: Path, env: dict[str, str] | None = None, wait_ready: bool = True):
        full_env = {k: v for k, v in os.environ.items() if not k.startswith("FERRO_")}
        full_env["PYTHONUNBUFFERED"] = "1"
        full_env.update(env or {})
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        process = subprocess.Popen(
            [sys.executable, "-m", "ferro", *args],
            cwd=cwd,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            creationflags=flags,
            start_new_session=sys.platform != "win32",
        )
        handle = FerroProcess(process)

        def pump() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                handle.lines.put(line)

        threading.Thread(target=pump, daemon=True).start()
        processes.append(handle)
        if wait_ready:
            handle.base_url = handle.wait_for(_BANNER.pattern).group(1)
        return handle

    yield _start
    for handle in processes:
        if handle.process.poll() is None:
            handle.process.kill()
            handle.process.wait(10)
