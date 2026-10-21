"""Feature 026: ``Depends(request_transaction)`` runs a request in one transaction, on SQLite
and PostgreSQL."""

from __future__ import annotations

import asyncio
import time

from ferro import App, BackgroundTasks, Depends, HTTPException, models
from ferro.db import request_transaction
from ferro.testing import TestClient

from .conftest import use_models


class Ledger(models.Model):
    note = models.String(max_length=40)

    class Meta:
        table = "t26_ledger"


def make_app(seen: list[int]) -> App:
    app = App()

    async def count_after(tasks: BackgroundTasks):
        async def record():
            seen.append(await Ledger.objects.count())

        tasks.add_task(record)

    @app.post("/ok")
    async def ok(_=Depends(request_transaction), __=Depends(count_after)):
        await Ledger.objects.create(note="one")
        await Ledger.objects.create(note="two")
        return {"inside": await Ledger.objects.count()}

    @app.post("/fail")
    async def fail(_=Depends(request_transaction)):
        await Ledger.objects.create(note="lost")
        raise HTTPException(409, "conflict")

    @app.post("/crash")
    async def crash(_=Depends(request_transaction)):
        await Ledger.objects.create(note="lost")
        raise RuntimeError("bug")

    @app.get("/count")
    async def count():
        return {"count": await Ledger.objects.count()}

    return app


def test_request_transaction_commits_and_rolls_back(database):
    asyncio.run(use_models(Ledger))
    seen: list[int] = []
    with TestClient(make_app(seen)) as client:
        assert client.post("/ok").json() == {"inside": 2}
        assert client.get("/count").json() == {"count": 2}
        deadline = time.monotonic() + 10
        while not seen and time.monotonic() < deadline:
            time.sleep(0.02)  # background tasks run after the response is sent
        assert seen == [2], "background tasks run after the commit"

        assert client.post("/fail").status_code == 409
        assert client.post("/crash").status_code == 500
        assert client.get("/count").json() == {"count": 2}, "both rolled back"
