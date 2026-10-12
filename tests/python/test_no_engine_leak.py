"""FR-029 — no type, error message or log line may name the engine, runtime or bridge library."""

import time

import pytest

from ferro import App, HTTPException

ENGINE_NAMES = ("actix", "tokio", "pyo3", "hyper", "mio")


def make_app() -> App:
    app = App()

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("handler failure")

    @app.get("/http-error")
    async def http_error():
        raise HTTPException(409)

    @app.get("/bad-return")
    async def bad_return():
        return {object()}

    @app.get("/slow")
    async def slow():
        import asyncio

        await asyncio.sleep(2)

    @app.post("/json")
    async def parse(request):
        return await request.json()

    return app


def _assert_clean(text: str, where: str) -> None:
    lowered = text.lower()
    for name in ENGINE_NAMES:
        assert name not in lowered, f"{where} mentions {name!r}: {text[:500]}"


@pytest.mark.parametrize("debug", [False, True])
def test_responses_and_logs_never_name_the_engine(serve, capfd, debug):
    client = serve(make_app(), debug=debug, request_timeout=0.3, max_body_size=64)
    requests = [
        ("GET", "/ok", {}),
        ("GET", "/boom", {}),
        ("GET", "/http-error", {}),
        ("GET", "/bad-return", {}),
        ("GET", "/slow", {}),
        ("GET", "/missing", {}),
        ("DELETE", "/ok", {}),
        ("GET", "/ok/", {}),
        ("POST", "/json", {"content": b"{bad", "headers": {"content-type": "application/json"}}),
        ("POST", "/json", {"content": b"x" * 1000}),
    ]
    for method, path, kwargs in requests:
        response = client.request(method, path, **kwargs)
        _assert_clean(str(dict(response.headers)), f"{method} {path} headers")
        _assert_clean(response.text, f"{method} {path} body")
    time.sleep(0.3)
    _assert_clean(capfd.readouterr().err, "server log")


def test_public_api_types_do_not_name_the_engine():
    import ferro

    for name in ferro.__all__:
        obj = getattr(ferro, name)
        _assert_clean(f"{obj!r} {getattr(obj, '__module__', '')}", name)
