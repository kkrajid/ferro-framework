"""Feature 006 US3 — rate limiting."""

from __future__ import annotations

import time

import pytest

from ferro import App, ConfigurationError


def limited_app(**settings) -> tuple[App, list[str]]:
    app = App(**settings)
    hits: list[str] = []

    @app.get("/")
    async def home(request):
        hits.append("home")
        return {"client": request.client[0]}

    @app.post("/login", rate_limit="2/minute")
    async def login():
        hits.append("login")
        return {"ok": True}

    return app, hits


def test_global_limit_refuses_with_retry_after(serve):
    app, hits = limited_app(rate_limit="3/minute")
    client = serve(app)
    assert [client.get("/").status_code for _ in range(3)] == [200, 200, 200]
    refused = client.get("/")
    assert refused.status_code == 429
    assert refused.json()["error"]["message"] == "Too Many Requests"
    assert refused.json()["error"]["request_id"] == refused.headers["x-request-id"]
    assert int(refused.headers["retry-after"]) == 20
    assert hits == ["home"] * 3


def test_route_limits_replace_the_global_one(serve):
    app, hits = limited_app(rate_limit="100/minute")
    client = serve(app)
    assert [client.post("/login").status_code for _ in range(3)] == [200, 200, 429]
    assert client.get("/").status_code == 200
    assert hits == ["login", "login", "home"]


def test_clients_are_counted_separately_behind_trusted_proxies(serve):
    app, _ = limited_app(rate_limit="1/minute", trusted_proxies=["127.0.0.1"])
    client = serve(app)
    first = client.get("/", headers={"x-forwarded-for": "203.0.113.1"})
    assert first.json() == {"client": "203.0.113.1"}
    assert client.get("/", headers={"x-forwarded-for": "203.0.113.2"}).status_code == 200
    assert client.get("/", headers={"x-forwarded-for": "203.0.113.1"}).status_code == 429
    # Without trust, the header is ignored.
    app, _ = limited_app(rate_limit="1/minute")
    untrusted = serve(app)
    assert untrusted.get("/", headers={"x-forwarded-for": "198.51.100.9"}).json() == {
        "client": "127.0.0.1"
    }
    assert untrusted.get("/", headers={"x-forwarded-for": "198.51.100.10"}).status_code == 429


def test_budget_recovers(serve):
    app, _ = limited_app(rate_limit="1/second")
    client = serve(app)
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 429
    time.sleep(1.1)
    assert client.get("/").status_code == 200


def test_invalid_limits_are_configuration_errors(serve):
    with pytest.raises(ConfigurationError, match="rate limit"):
        serve(limited_app(rate_limit="10/fortnight")[0])
    with pytest.raises(ConfigurationError, match="rate limit"):
        App().get("/x", rate_limit="0/minute")
    with pytest.raises(ConfigurationError, match="not an IP address"):
        serve(limited_app(trusted_proxies=["nonsense"])[0])
