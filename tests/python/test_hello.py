"""User Story 1 — Hello World (spec.md US1, FR-001, FR-002, FR-004, FR-012, FR-026, FR-027)."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from ferro import App, ConfigurationError


def make_app() -> App:
    app = App()

    @app.get("/")
    async def home():
        return {"message": "Hello from Rust"}

    @app.get("/whoami")
    async def whoami(request):
        return {"method": request.method, "path": request.path, "id": request.request_id}

    return app


def test_get_root_returns_json(serve):
    client = serve(make_app())
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"message": "Hello from Rust"}


def test_baseline_headers(serve):
    response = serve(make_app()).get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "server" not in response.headers
    assert len(response.headers["x-request-id"]) == 32


def test_unknown_path_is_404_envelope(serve):
    response = serve(make_app()).get("/missing")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["status"] == 404
    assert error["message"] == "Not Found"
    assert error["request_id"] == response.headers["x-request-id"]


def test_wrong_method_is_405_with_allow(serve):
    response = serve(make_app()).post("/")
    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"
    assert response.json()["error"]["status"] == 405


def test_head_is_answered_by_get_route(serve):
    response = serve(make_app()).head("/")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.content == b""


def test_handler_receives_request(serve):
    client = serve(make_app())
    response = client.get("/whoami", headers={"x-request-id": "abc-1"})
    assert response.json() == {"method": "GET", "path": "/whoami", "id": "abc-1"}
    assert response.headers["x-request-id"] == "abc-1"


def test_concurrent_requests(serve):
    client = serve(make_app())
    with ThreadPoolExecutor(max_workers=16) as pool:
        statuses = list(pool.map(lambda _: client.get("/").status_code, range(200)))
    assert statuses == [200] * 200


def test_decorator_returns_the_original_handler():
    app = App()

    @app.get("/")
    async def home():
        return {"ok": True}

    assert asyncio.run(home()) == {"ok": True}


def test_routes_listing():
    app = make_app()
    assert [(r.methods, r.path) for r in app.routes] == [(("GET",), "/"), (("GET",), "/whoami")]
    assert app.routes[0].name.endswith("make_app.<locals>.home")


@pytest.mark.parametrize("path", ["no-slash", "/a/{", "/a//b", "/a/{1}"])
def test_invalid_paths_fail_at_decoration(path):
    app = App()
    with pytest.raises(ConfigurationError, match="invalid route path"):
        app.get(path)


def test_duplicate_routes_fail_when_serving(serve):
    app = App()

    @app.get("/x")
    async def first():
        return 1

    @app.get("/x")
    async def second():
        return 2

    with pytest.raises(ConfigurationError, match=r"duplicate route: GET /x.*second.*first"):
        serve(app)


def test_non_callable_handler_is_rejected():
    app = App()
    with pytest.raises(ConfigurationError, match="callable"):
        app.get("/")("not a function")
