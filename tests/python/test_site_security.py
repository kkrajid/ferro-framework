"""Feature 023: allowed hosts, HTTPS redirects and HSTS options."""

from __future__ import annotations

import pytest

from ferro import App, ConfigurationError


def hello(**settings) -> App:
    app = App(**settings)

    @app.get("/hello")
    async def hello_view():
        return {"ok": True}

    @app.post("/echo")
    async def echo(request):
        return {"body": (await request.body()).decode()}

    return app


def test_allowed_hosts(serve):
    client = serve(hello(allowed_hosts=["example.com", ".shop.test"]))
    assert client.get("/hello", headers={"host": "example.com"}).status_code == 200
    assert client.get("/hello", headers={"host": "EXAMPLE.com:8000"}).status_code == 200
    assert client.get("/hello", headers={"host": "api.shop.test"}).status_code == 200
    refused = client.get("/hello", headers={"host": "evil.com"})
    assert refused.status_code == 400
    assert refused.json()["error"]["message"] == "Invalid Host header"
    assert client.get("/hello").status_code == 400  # 127.0.0.1:port is not listed
    assert client.get("/missing", headers={"host": "evil.com"}).status_code == 400
    # Probes stay reachable by address (load balancers).
    assert client.get("/healthz").status_code == 200
    assert serve(hello()).get("/hello", headers={"host": "anything"}).status_code == 200
    with pytest.raises(ConfigurationError, match="allowed_hosts"):
        serve(hello(allowed_hosts=["example.com:80"]))


def test_https_redirect_behind_a_proxy(serve):
    client = serve(
        hello(https_redirect=True, trusted_proxies=["127.0.0.1"], allowed_hosts=["example.com"])
    )
    plain = client.get("/hello?x=1", headers={"host": "example.com", "x-forwarded-proto": "http"})
    assert plain.status_code == 308
    assert plain.headers["location"] == "https://example.com/hello?x=1"
    secure = client.get("/hello", headers={"host": "example.com", "x-forwarded-proto": "https"})
    assert secure.status_code == 200
    posted = client.post("/echo", content=b"data", headers={"host": "example.com"})
    assert posted.status_code == 308, "308 keeps the method and body"
    assert client.get("/healthz").status_code == 200
    assert client.get("/hello", headers={"host": "evil.com"}).status_code == 400


def test_hsts_options(serve):
    plain = serve(hello(hsts=True, hsts_include_subdomains=False)).get("/hello")
    assert plain.headers["strict-transport-security"] == "max-age=31536000"
    preload = serve(hello(hsts=True, hsts_preload=True)).get("/hello")
    assert (
        preload.headers["strict-transport-security"]
        == "max-age=31536000; includeSubDomains; preload"
    )
    with pytest.raises(ConfigurationError, match="hsts_preload"):
        serve(hello(hsts=True, hsts_preload=True, hsts_max_age=600))
