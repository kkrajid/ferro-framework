"""Feature 011 US4 / SC-004 — request ids in a real server's log output, text and JSON."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.support.postgres import free_port

APP = """import asyncio
import logging
import os

from ferro import App

app = App()
log = logging.getLogger("shop")


@app.get("/pid")
async def pid():
    return {"pid": os.getpid()}


@app.get("/async/{token}")
async def async_handler(token: str):
    await asyncio.sleep(0.01)
    log.info("token %s", token)
    return {}


@app.get("/sync/{token}")
def sync_handler(token: str):
    log.info("token %s", token)
    return {}

"""


def run_and_log(tmp_path: Path, *args: str) -> tuple[dict[str, str], str]:
    (tmp_path / "app.py").write_text(APP)
    port = free_port()
    environment = {k: v for k, v in os.environ.items() if not k.startswith("FERRO_")}
    log_path = tmp_path / "stderr.log"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    with log_path.open("wb") as log:
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
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    httpx.get(f"{base}/pid", timeout=1)
                    break
                except httpx.HTTPError:
                    assert time.monotonic() < deadline, log_path.read_text()
                    time.sleep(0.1)
            expected = {}
            for i in range(50):
                kind = "async" if i % 2 else "sync"
                token = f"{kind}-{i}"
                response = httpx.get(f"{base}/{kind}/{token}", timeout=10)
                expected[token] = response.headers["x-request-id"]
        finally:
            if sys.platform == "win32":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGTERM)
            process.wait(30)
    return expected, log_path.read_text(encoding="utf-8")


def test_text_lines_carry_the_request_id(tmp_path):
    expected, output = run_and_log(tmp_path)
    for token, request_id in expected.items():
        lines = [line for line in output.splitlines() if f"token {token} logger=" in line]
        assert len(lines) == 1, (token, lines)
        assert f"request_id={request_id}" in lines[0], lines[0]
        assert "logger=shop" in lines[0]


@pytest.mark.parametrize("option", [("--log-format", "json")])
def test_json_lines(tmp_path, option):
    expected, output = run_and_log(tmp_path, *option)
    records = []
    for line in output.splitlines():
        if line.startswith("Ferro "):  # the startup banner is not a log record
            continue
        records.append(json.loads(line))
    for record in records:
        assert {"timestamp", "level", "target"} <= set(record), record
    application = {r["message"].split()[-1]: r for r in records if r.get("logger") == "shop"}
    for token, request_id in expected.items():
        assert application[token]["request_id"] == request_id
        assert application[token]["target"] == "ferro.python"
    access = [r for r in records if r["target"] == "ferro.access"]
    assert {r["request_id"] for r in access} >= set(expected.values())
    assert all(isinstance(r["status"], int) for r in access)
