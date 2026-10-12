"""Feature 011 US4 — request ids in application log records."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import httpx

from ferro import App, log
from ferro.testing import TestClient


def test_records_outside_requests_have_no_request_id():
    log.configure("info")
    record = logging.getLogger("shop").makeRecord(
        "shop", logging.INFO, __file__, 1, "outside", (), None
    )
    assert record.request_id is None


def test_handlers_log_with_their_own_request_id(caplog):
    app = App()

    @app.get("/async/{token}")
    async def async_handler(token: str):
        await asyncio.sleep(0.01)
        logging.getLogger("shop").warning("token %s", token)
        return {}

    @app.get("/sync/{token}")
    def sync_handler(token: str):
        logging.getLogger("shop").warning("token %s", token)
        return {}

    with TestClient(app) as client, caplog.at_level(logging.WARNING, logger="shop"):
        base = f"http://127.0.0.1:{client._address[1]}"

        def call(i: int) -> tuple[str, str]:
            kind = "async" if i % 2 else "sync"
            token = f"{kind}-{i}"
            response = httpx.get(f"{base}/{kind}/{token}", timeout=10)
            return token, response.headers["x-request-id"]

        with ThreadPoolExecutor(16) as pool:
            expected = dict(pool.map(call, range(40)))
    records = {r.getMessage().split()[-1]: r.request_id for r in caplog.records if r.name == "shop"}
    assert records == expected


def test_rust_handler_forwards_the_request_id(monkeypatch):
    calls = []
    monkeypatch.setattr(log._ferro, "log", lambda *args: calls.append(args))
    record = logging.makeLogRecord(
        {"name": "shop", "levelname": "INFO", "msg": "hello", "request_id": "abc-1"}
    )
    log.RustHandler().emit(record)
    assert calls == [("INFO", "shop", "hello", "abc-1")]


def test_configure_attaches_only_to_an_unconfigured_root(monkeypatch):
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", root.level)
    log.configure("info")
    assert [type(h) for h in root.handlers] == [log.RustHandler]
    assert root.level == logging.INFO
    log.configure("debug")
    assert len(root.handlers) == 1, "configuring again adds nothing"
    assert root.level == logging.DEBUG

    own = logging.StreamHandler()
    monkeypatch.setattr(root, "handlers", [own])
    monkeypatch.setattr(root, "level", logging.WARNING)
    log.configure("info")
    assert root.handlers == [own], "an application's own logging configuration stays"
    assert root.level == logging.WARNING
