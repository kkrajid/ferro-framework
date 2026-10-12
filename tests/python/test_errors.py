"""User Story 4 — safe, helpful error handling (FR-015-FR-019, SC-006)."""

import asyncio
import time

import pytest

from ferro import App, HTTPException

state: dict[str, object] = {}


def make_app() -> App:
    app = App()

    @app.get("/orders/{id}")
    async def order(id: int):
        raise HTTPException(404, "Order not found", headers={"x-reason": "missing"})

    @app.get("/teapot")
    async def teapot():
        raise HTTPException(418)

    @app.get("/boom")
    async def boom():
        raise ValueError("secret detail from C:\\app\\settings.py")

    @app.get("/unserializable")
    async def unserializable():
        return {"items": {1, 2, 3}}

    @app.get("/sleepy")
    async def sleepy():
        state["started"] = True
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        state["finished"] = True
        return {"done": True}

    @app.get("/fast")
    async def fast():
        return {"fast": True}

    return app


@pytest.fixture(autouse=True)
def _reset():
    state.clear()


def test_http_exception_envelope(serve):
    response = serve(make_app()).get("/orders/7")
    assert response.status_code == 404
    assert response.headers["x-reason"] == "missing"
    body = response.json()
    assert body == {
        "error": {
            "status": 404,
            "message": "Order not found",
            "request_id": response.headers["x-request-id"],
        }
    }


def test_http_exception_default_message(serve):
    response = serve(make_app()).get("/teapot")
    assert response.status_code == 418
    assert response.json()["error"]["message"] == "I'm a Teapot"


def test_production_500_reveals_nothing(serve, capfd):
    response = serve(make_app(), debug=False).get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert set(body["error"]) == {"status", "message", "request_id"}
    assert body["error"]["message"] == "Internal Server Error"
    text = response.text.lower()
    for leak in ["secret", "valueerror", "traceback", "settings.py", "actix", "tokio", "pyo3"]:
        assert leak not in text
    time.sleep(0.2)
    log = capfd.readouterr().err
    assert "ValueError" in log
    assert "secret detail" in log
    assert body["error"]["request_id"] in log
    assert "Traceback" in log


def test_debug_500_includes_exception_detail(serve):
    response = serve(make_app(), debug=True).get("/boom")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["message"] == "Internal Server Error"
    assert error["type"] == "ValueError"
    assert "secret detail" in error["exception"]
    assert any("raise ValueError" in line for line in error["traceback"])


def test_unserializable_return_is_500(serve):
    response = serve(make_app(), debug=True).get("/unserializable")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["type"] == "TypeError"
    assert "set" in error["exception"]


def test_timeout_is_503_and_cancels_handler(serve):
    client = serve(make_app(), request_timeout=0.3)
    started = time.monotonic()
    response = client.get("/sleepy")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json()["error"]["status"] == 503
    assert time.monotonic() - started < 3
    deadline = time.monotonic() + 3
    while "cancelled" not in state and time.monotonic() < deadline:
        time.sleep(0.05)
    assert state.get("cancelled") is True
    assert "finished" not in state
    assert client.get("/fast").json() == {"fast": True}


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [("GET", "/nope", 404), ("DELETE", "/fast", 405), ("GET", "/orders/x", 422)],
)
def test_framework_errors_share_envelope(serve, method, path, status):
    response = serve(make_app()).request(method, path)
    assert response.status_code == status
    error = response.json()["error"]
    assert error["status"] == status
    assert isinstance(error["message"], str)
    assert error["request_id"] == response.headers["x-request-id"]


def test_http_exception_validates_status():
    with pytest.raises(ValueError, match="400 to 599"):
        HTTPException(200)
