"""Feature 006 US2 — CSRF origin check, CORS, security headers and cookies."""

from __future__ import annotations

import pytest

from ferro import CORS, App, ConfigurationError, Response

EVIL = "https://evil.example"
APP = "https://app.example"


def counted_app(**settings) -> tuple[App, list[str]]:
    app = App(**settings)
    hits: list[str] = []

    @app.post("/items")
    async def create():
        hits.append("items")
        return {"created": True}

    @app.post("/hook", csrf=False)
    async def hook():
        hits.append("hook")
        return {"received": True}

    @app.get("/items")
    async def listing():
        hits.append("list")
        return []

    return app, hits


def own_origin(client) -> str:
    return str(client.base_url).rstrip("/")


def test_cross_origin_writes_are_refused_before_python(serve):
    app, hits = counted_app()
    client = serve(app)
    for headers in (
        {"origin": EVIL},
        {"sec-fetch-site": "cross-site", "origin": EVIL},
        {"origin": "null"},
        {"sec-fetch-site": "same-site", "origin": "http://sub.localhost"},
    ):
        response = client.post("/items", headers=headers)
        assert response.status_code == 403, headers
        assert response.json()["error"]["message"] == "Cross-origin request refused"
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert hits == []
    assert client.get("/items", headers={"origin": EVIL}).status_code == 200
    assert client.post("/items").status_code == 200, "non-browser clients"
    assert client.post("/items", headers={"origin": own_origin(client)}).status_code == 200
    assert client.post("/items", headers={"sec-fetch-site": "same-origin"}).status_code == 200
    assert client.post("/hook", headers={"origin": EVIL}).status_code == 200
    assert hits == ["list", "items", "items", "items", "hook"]


def test_trusted_origins_and_disabling(serve):
    app, _ = counted_app(trusted_origins=[APP])
    client = serve(app)
    assert client.post("/items", headers={"origin": APP}).status_code == 200
    assert client.post("/items", headers={"origin": EVIL}).status_code == 403
    app, _ = counted_app(csrf=False)
    assert serve(app).post("/items", headers={"origin": EVIL}).status_code == 200
    with pytest.raises(ConfigurationError, match="not an origin"):
        serve(counted_app(trusted_origins=["app.example"])[0])


def test_cors_preflight_and_actual_responses(serve):
    cors = CORS(
        allow_origins=[APP],
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
        expose_headers=["x-total"],
        allow_credentials=True,
        max_age=120,
    )
    app, hits = counted_app(cors=cors)
    client = serve(app)
    preflight = {"origin": APP, "access-control-request-method": "POST"}
    for path in ("/items", "/does-not-exist"):
        response = client.options(
            path, headers={**preflight, "access-control-request-headers": "Content-Type"}
        )
        assert response.status_code == 204, path
        assert response.headers["access-control-allow-origin"] == APP
        assert response.headers["access-control-allow-methods"] == "GET, POST"
        assert response.headers["access-control-allow-headers"] == "content-type"
        assert response.headers["access-control-allow-credentials"] == "true"
        assert response.headers["access-control-max-age"] == "120"
    denied = client.options("/items", headers={**preflight, "origin": EVIL})
    assert denied.status_code == 403
    assert "access-control-allow-origin" not in denied.headers
    assert hits == []

    response = client.get("/items", headers={"origin": APP})
    assert response.headers["access-control-allow-origin"] == APP
    assert response.headers["access-control-expose-headers"] == "x-total"
    assert "origin" in response.headers["vary"].lower()
    assert (
        "access-control-allow-origin" not in client.get("/items", headers={"origin": EVIL}).headers
    )
    # CORS-allowed origins may also write.
    assert client.post("/items", headers={"origin": APP}).status_code == 200


def test_cors_configuration_is_validated():
    with pytest.raises(ConfigurationError, match="allow_credentials"):
        CORS(allow_origins=["*"], allow_credentials=True)
    open_cors = CORS(allow_origins=["*"])
    assert open_cors.allow_origins == ("*",)


def test_default_and_optional_security_headers(serve):
    app = App()

    @app.get("/")
    async def home():
        return {}

    @app.get("/framed")
    async def framed():
        return Response({}, headers={"x-frame-options": "SAMEORIGIN"})

    client = serve(app)
    headers = client.get("/").headers
    assert headers["x-frame-options"] == "DENY"
    assert headers["cross-origin-opener-policy"] == "same-origin"
    assert headers["x-content-type-options"] == "nosniff"
    assert "strict-transport-security" not in headers
    assert client.get("/framed").headers["x-frame-options"] == "SAMEORIGIN"
    assert client.get("/missing").headers["x-frame-options"] == "DENY"

    strict = App(hsts=True, hsts_max_age=600, content_security_policy="default-src 'self'")
    strict.get("/")(lambda: {})
    headers = serve(strict).get("/").headers
    assert headers["strict-transport-security"] == "max-age=600; includeSubDomains"
    assert headers["content-security-policy"] == "default-src 'self'"


def cookie_app() -> App:
    app = App()

    @app.get("/cookies")
    async def cookies(request):
        return request.cookies

    @app.get("/login")
    async def login():
        response = Response({"ok": True})
        response.set_cookie("sid", "abc123", max_age=3600)
        response.set_cookie("theme", "dark", httponly=False, samesite="strict", path="/app")
        response.delete_cookie("old")
        return response

    return app


def test_request_cookies_are_parsed(serve):
    client = serve(cookie_app())
    response = client.get("/cookies", headers={"cookie": 'a=1; b="two words"; flag; a=3'})
    assert response.json() == {"a": "3", "b": "two words"}
    assert client.get("/cookies").json() == {}


def test_set_cookie_defaults_depend_on_mode(serve):
    production = serve(cookie_app(), debug=False).get("/login")
    cookies = production.headers.get_list("set-cookie")
    assert cookies[0] == "sid=abc123; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax; Secure"
    assert cookies[1] == "theme=dark; Path=/app; SameSite=Strict; Secure"
    assert cookies[2].startswith("old=; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT")
    development = serve(cookie_app(), debug=True).get("/login")
    assert "Secure" not in development.headers.get_list("set-cookie")[0]


def test_invalid_cookies_are_rejected():
    response = Response()
    for name, value in (("bad name", "x"), ("ok", "semi;colon"), ("ok", "new\nline"), ("", "x")):
        with pytest.raises(ValueError):
            response.set_cookie(name, value)
    with pytest.raises(ValueError, match="samesite"):
        response.set_cookie("ok", "x", samesite="sometimes")
    with pytest.raises(ValueError, match="secure"):
        response.set_cookie("ok", "x", samesite="none", secure=False)


def test_security_settings_from_the_environment():
    from ferro.config import resolve

    settings = resolve(
        env={
            "FERRO_TRUSTED_ORIGINS": "https://a.example, https://b.example",
            "FERRO_CSRF": "false",
            "FERRO_RATE_LIMIT": "50/minute",
            "FERRO_HSTS": "yes",
        },
        app={"rate_limit": "10/second", "trusted_proxies": ["10.0.0.0/8"]},
    )
    assert settings.trusted_origins == ("https://a.example", "https://b.example")
    assert settings.csrf is False
    assert settings.rate_limit == "50/minute", "environment beats App(...)"
    assert settings.trusted_proxies == ("10.0.0.0/8",)
    assert settings.hsts is True
    with pytest.raises(ConfigurationError, match="rate_limit"):
        resolve(env={"FERRO_RATE_LIMIT": "lots"})
    with pytest.raises(ConfigurationError, match="CORS"):
        resolve(env={"FERRO_CORS": "*"})
