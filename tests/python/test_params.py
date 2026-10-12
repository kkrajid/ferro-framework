"""User Story 2 — path and query parameters (FR-003, FR-004a, FR-005, FR-007, FR-008, FR-009)."""

from __future__ import annotations

from typing import Optional

import pytest

from ferro import App, ConfigurationError, Request


def make_app() -> App:
    app = App()

    @app.get("/products/{id}")
    async def product(request, id: int, currency: str = "INR"):
        return {"id": id, "currency": currency, "path": request.path}

    @app.get("/search")
    async def search(
        q: str,
        page: int = 1,
        exact: bool = False,
        limit: Optional[int] = None,  # noqa: UP045
        ratio: float | None = 1.0,
    ):
        return {"q": q, "page": page, "exact": exact, "limit": limit, "ratio": ratio}

    @app.get("/tags")
    async def tags(req: Request):
        query = req.query
        return {
            "all": query.getlist("tag"),
            "last": query.get("tag"),
            "fallback": query.get("nope", "dflt"),
            "keys": list(query),
            "len": len(query),
            "item": query["x"],
            "has_x": "x" in query,
            "raw": req.query_string,
        }

    @app.get("/users/me")
    async def me():
        return {"who": "me"}

    @app.get("/users/{id}")
    async def user(id):
        return {"who": id, "type": type(id).__name__}

    @app.get("/files/{name}")
    async def file(request, name: str):
        return {"name": name, "params": request.path_params, "raw": request.raw_path}

    @app.get("/headers")
    async def headers(request):
        return {
            "agent": request.headers.get("User-Agent"),
            "multi": request.headers.getlist("X-Multi"),
            "has": "x-multi" in request.headers,
            "item": request.headers["X-MULTI"],
            "missing": request.headers.get("x-none"),
            "client": request.client[0],
        }

    return app


@pytest.fixture
def client(serve):
    return serve(make_app())


def test_path_param_converted_and_query_default(client):
    response = client.get("/products/42")
    assert response.json() == {"id": 42, "currency": "INR", "path": "/products/42"}


def test_query_param_overrides_default(client):
    assert client.get("/products/42?currency=USD").json()["currency"] == "USD"


def test_bad_path_value_is_422(client):
    response = client.get("/products/abc")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["message"] == "Validation failed"
    assert error["details"] == [
        {"param": "id", "location": "path", "problem": "expected int, got 'abc'"}
    ]


def test_missing_required_query_is_422(client):
    response = client.get("/search")
    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {"param": "q", "location": "query", "problem": "missing required query parameter"}
    ]


def test_all_problems_are_reported_together(client):
    details = client.get("/search?page=x&exact=maybe").json()["error"]["details"]
    assert [d["param"] for d in details] == ["q", "page", "exact"]


def test_query_conversions(client):
    response = client.get("/search?q=laptop&page=2&exact=YES&limit=5&ratio=0.5")
    assert response.json() == {"q": "laptop", "page": 2, "exact": True, "limit": 5, "ratio": 0.5}
    response = client.get("/search?q=x&exact=off")
    assert response.json() == {"q": "x", "page": 1, "exact": False, "limit": None, "ratio": 1.0}


def test_query_params_object(client):
    body = client.get("/tags?tag=a&tag=b&x=1+2&x=%26").json()
    assert body == {
        "all": ["a", "b"],
        "last": "b",
        "fallback": "dflt",
        "keys": ["tag", "x"],
        "len": 2,
        "item": "&",
        "has_x": True,
        "raw": "tag=a&tag=b&x=1+2&x=%26",
    }


def test_unannotated_param_is_str(client):
    assert client.get("/users/42").json() == {"who": "42", "type": "str"}


@pytest.mark.parametrize("reverse", [False, True])
def test_static_route_beats_parameter_route(serve, reverse):
    app = App()

    async def me():
        return {"who": "me"}

    async def user(id):
        return {"who": id}

    order = (
        [("/users/{id}", user), ("/users/me", me)]
        if reverse
        else [
            ("/users/me", me),
            ("/users/{id}", user),
        ]
    )
    for path, handler in order:
        app.get(path)(handler)
    client = serve(app)
    assert client.get("/users/me").json() == {"who": "me"}
    assert client.get("/users/7").json() == {"who": "7"}


def test_trailing_slash_redirect_keeps_query(client):
    response = client.get("/products/42/?currency=USD")
    assert response.status_code == 308
    assert response.headers["location"] == "/products/42?currency=USD"


def test_path_params_are_decoded(client):
    assert client.get("/files/a%20b").json()["name"] == "a b"
    body = client.get("/files/caf%C3%A9").json()
    assert body["name"] == "café"
    assert body["params"] == {"name": "café"}
    assert body["raw"] == "/files/caf%C3%A9"
    assert client.get("/files/a%2Fb").json()["name"] == "a/b"


def test_headers_are_case_insensitive_and_multi_valued(client):
    response = client.get(
        "/headers",
        headers=[("User-Agent", "pytest"), ("X-Multi", "1"), ("X-Multi", "2")],
    )
    body = response.json()
    assert body["agent"] == "pytest"
    assert body["multi"] in (["1", "2"], ["1, 2"])
    assert body["has"] is True
    assert body["item"] in ("1", "1, 2")
    assert body["missing"] is None
    assert body["client"] == "127.0.0.1"


def _register(handler):
    App().get("/things/{id}")(handler)


def test_var_args_rejected():
    async def handler(*args):
        return None

    with pytest.raises(ConfigurationError, match=r"handler .*handler: parameter '\*args'"):
        _register(handler)


def test_var_kwargs_rejected():
    async def handler(**kwargs):
        return None

    with pytest.raises(ConfigurationError, match=r"parameter '\*\*kwargs'"):
        _register(handler)


def test_positional_only_rejected():
    async def handler(id, /):
        return None

    with pytest.raises(ConfigurationError, match="positional-only"):
        _register(handler)


def test_unsupported_annotation_rejected():
    async def handler(id: list[int]):
        return None

    with pytest.raises(ConfigurationError, match=r"parameter 'id'.*list\[int\]"):
        _register(handler)


def test_unresolvable_annotation_rejected():
    async def handler(id: NotDefinedAnywhere):  # noqa: F821
        return None

    with pytest.raises(ConfigurationError, match="NotDefinedAnywhere"):
        _register(handler)
