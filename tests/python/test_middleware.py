"""Feature 006 US1 — Python middleware."""

from __future__ import annotations

from ferro import App, HTTPException, Response

KINDS = {
    "/dict": {"a": 1},
    "/list": [1, 2],
    "/text": "hello",
    "/bytes": b"\x00raw",
    "/none": None,
}


def kinds_app(with_middleware: bool, seen: list[str]) -> App:
    app = App()
    if with_middleware:

        @app.middleware
        async def record(request, call_next):
            response = await call_next(request)
            seen.append(type(response).__name__)
            return response

    for path, value in KINDS.items():
        app.get(path)(lambda value=value: value)

    @app.get("/explicit")
    async def explicit():
        return Response({"made": True}, status=201, headers={"x-made": "1"})

    return app


def test_order_short_circuit_and_header_changes(serve):
    app = App()
    calls: list[str] = []

    @app.middleware
    async def outer(request, call_next):
        calls.append("outer in")
        response = await call_next(request)
        calls.append("outer out")
        response.headers.append(("x-outer", "1"))
        return response

    @app.middleware
    async def inner(request, call_next):
        calls.append("inner in")
        if request.path == "/blocked":
            return Response({"blocked": True}, status=401)
        response = await call_next(request)
        calls.append("inner out")
        return response

    @app.get("/")
    async def home():
        calls.append("handler")
        return {"ok": True}

    @app.get("/blocked")
    async def blocked():
        calls.append("never")

    client = serve(app)
    response = client.get("/")
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert response.headers["x-outer"] == "1"
    assert calls == ["outer in", "inner in", "handler", "inner out", "outer out"]
    calls.clear()
    response = client.get("/blocked")
    assert response.status_code == 401 and response.json() == {"blocked": True}
    assert response.headers["x-outer"] == "1"
    assert "never" not in calls


def test_every_result_kind_becomes_the_same_response(serve):
    seen: list[str] = []
    plain = serve(kinds_app(False, seen))
    wrapped = serve(kinds_app(True, seen))
    for path in [*KINDS, "/explicit"]:
        a, b = plain.get(path), wrapped.get(path)
        assert (a.status_code, a.headers.get("content-type"), a.content) == (
            b.status_code,
            b.headers.get("content-type"),
            b.content,
        ), path
        assert a.headers.get("x-made") == b.headers.get("x-made")
    assert seen == ["Response"] * (len(KINDS) + 1)


def test_exceptions_pass_through_call_next(serve):
    app = App()

    @app.middleware
    async def translate(request, call_next):
        try:
            return await call_next(request)
        except ValueError:
            return Response({"handled": True}, status=400)

    @app.get("/value")
    async def value():
        raise ValueError("bad")

    @app.get("/teapot")
    async def teapot():
        raise HTTPException(418)

    client = serve(app)
    assert client.get("/value").json() == {"handled": True}
    response = client.get("/teapot")
    assert response.status_code == 418
    assert response.json()["error"]["status"] == 418


def test_request_state_reaches_the_handler(serve):
    app = App()

    @app.middleware
    async def user(request, call_next):
        request.state.user = request.headers.get("x-user", "anonymous")
        return await call_next(request)

    @app.get("/me")
    async def me(request):
        return {"user": request.state.user}

    client = serve(app)
    assert client.get("/me", headers={"x-user": "ann"}).json() == {"user": "ann"}
    assert client.get("/me").json() == {"user": "anonymous"}


def test_misbehaving_middleware_is_a_500(serve):
    app = App()

    @app.middleware
    async def twice(request, call_next):
        if request.path == "/twice":
            await call_next(request)
            return await call_next(request)
        if request.path == "/nothing":
            return {"not": "a Response"}
        return await call_next(request)

    for path in ("/twice", "/nothing", "/fine"):
        app.get(path)(lambda: {"ok": True})

    client = serve(app, debug=True)
    response = client.get("/twice")
    assert response.status_code == 500
    assert "call_next" in response.json()["error"]["exception"]
    response = client.get("/nothing")
    assert response.status_code == 500
    assert "twice" in response.json()["error"]["exception"]
    assert client.get("/fine").json() == {"ok": True}


def test_middleware_must_be_async():
    app = App()
    try:

        @app.middleware
        def sync(request, call_next):  # pragma: no cover - rejected at registration
            return call_next(request)

    except TypeError as error:
        assert "async def" in str(error)
    else:
        raise AssertionError("a sync middleware was accepted")
