"""Feature 011 US5 — metrics of a real application, including database queries."""

from __future__ import annotations

import re
from pathlib import Path

from ferro import App, __version__, models
from ferro.testing import TestClient


class Thing(models.Model):
    name = models.String(max_length=20)

    class Meta:
        table = "t11_thing"


class Missing(models.Model):
    name = models.String(max_length=20)

    class Meta:
        table = "t11_missing"


def metrics_app(**settings) -> App:
    app = App(**settings)

    @app.get("/things/{name}")
    async def things(name: str):
        return {"count": await Thing.objects.filter(name=name).count()}

    @app.get("/missing")
    async def missing():
        return {"count": await Missing.objects.count()}

    return app


def series(text: str, name: str) -> float:
    match = re.search(rf"^{re.escape(name)} (\S+)$", text, re.MULTILINE)
    return float(match.group(1)) if match else 0.0


def test_metrics_are_off_by_default():
    with TestClient(metrics_app()) as client:
        assert client.get("/metrics").status_code == 404


def test_application_and_database_metrics(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'metrics.db').as_posix()}"
    with TestClient(metrics_app(metrics=True), database_url=url, create_tables=[Thing]) as client:
        before = client.get("/metrics").text
        for _ in range(3):
            assert client.get("/things/a").status_code == 200
        assert client.get("/missing").status_code == 500
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    text = response.text
    route = 'ferro_http_requests_total{route="/things/{name}",method="GET",status="2xx"}'
    assert series(text, route) == 3
    failed = 'ferro_http_requests_total{route="/missing",method="GET",status="5xx"}'
    assert series(text, failed) == 1
    for outcome, at_least in (("ok", 3), ("error", 1)):
        name = f'ferro_db_queries_total{{outcome="{outcome}"}}'
        assert series(text, name) - series(before, name) >= at_least, outcome
    assert series(text, "ferro_db_query_duration_seconds_count") >= 4
    assert f'ferro_build_info{{version="{__version__}"}} 1' in text
