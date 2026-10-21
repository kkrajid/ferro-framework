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


def test_ipv6_hosts_and_websockets(serve):
    from ferro.testing import TestClient, WebSocketRejected

    client = serve(hello(allowed_hosts=["[::1]", "example.com"]))
    assert client.get("/hello", headers={"host": "[::1]:8000"}).status_code == 200
    assert client.get("/hello", headers={"host": "[::2]"}).status_code == 400
    app = hello(allowed_hosts=["example.com"])

    @app.websocket("/ws")
    async def socket(ws):
        await ws.send("hi")

    with TestClient(app) as test:
        with test.websocket_connect("/ws", headers={"host": "example.com"}) as ws:
            assert ws.receive() == "hi"
        with pytest.raises(WebSocketRejected) as refused:
            test.websocket_connect("/ws", headers={"host": "evil.com"}).__enter__()
        assert refused.value.response.status_code == 400


def test_no_redirect_when_ferro_serves_https(tmp_path):
    import ssl

    import httpx

    trustme = pytest.importorskip("trustme")
    from ferro.config import resolve
    from ferro.server import ServerRunner

    ca = trustme.CA()
    issued = ca.issue_cert("localhost", "127.0.0.1")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert.write_bytes(b"".join(blob.bytes() for blob in issued.cert_chain_pems))
    issued.private_key_pem.write_to_path(str(key))
    context = ssl.create_default_context()
    ca.configure_trust(context)
    app = hello(https_redirect=True)
    cli = {"host": "127.0.0.1", "port": 0, "workers": 1, "tls_cert": str(cert), "tls_key": str(key)}
    runner = ServerRunner(app, resolve(cli=cli, env={}, app=app._settings, mode="run"))
    _, port = runner.start()
    try:
        with httpx.Client(verify=context, timeout=10) as client:
            response = client.get(f"https://127.0.0.1:{port}/hello")
        assert response.status_code == 200, "already HTTPS: no redirect loop"
    finally:
        runner.stop(graceful=True)
        runner.wait(10)
        runner.close()
