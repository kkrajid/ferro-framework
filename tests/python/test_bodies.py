"""User Story 3 — JSON APIs with every HTTP method (FR-002, FR-009-FR-014)."""

import datetime as dt
import uuid
from decimal import Decimal

import pytest

from ferro import App, JSONResponse, Response

calls: list[str] = []


def make_app() -> App:
    app = App(max_body_size=1024 * 1024)

    @app.post("/products")
    async def create(request):
        data = await request.json()
        calls.append("create-after-json")
        return Response(data, status=201, headers={"location": "/products/1"})

    @app.put("/products/{id}")
    async def replace(id: int, request):
        return {"replaced": id, "body": await request.json()}

    @app.patch("/products/{id}")
    async def update(id: int):
        return {"patched": id}

    @app.delete("/products/{id}")
    async def remove(id: int):
        return None

    @app.route("/multi", methods=["GET", "post"])
    async def multi(request):
        return {"method": request.method}

    @app.post("/raw")
    async def raw(request):
        body = await request.body()
        calls.append("raw")
        return {"length": len(body), "type": type(body).__name__}

    @app.get("/text")
    async def text():
        return "plain text"

    @app.get("/bytes")
    async def binary():
        return b"\x00\x01binary"

    @app.get("/bytearray")
    async def mutable_bytes():
        return bytearray(b"abc")

    @app.get("/tuple")
    async def as_tuple():
        return (1, "two")

    @app.get("/json-string")
    async def json_string():
        return JSONResponse("just a string", status=202)

    @app.get("/custom")
    async def custom():
        return Response(
            "<h1>hi</h1>",
            media_type="text/html; charset=utf-8",
            headers=[("x-one", "1"), ("set-cookie", "a=1"), ("set-cookie", "b=2")],
        )

    @app.get("/empty-response")
    async def empty_response():
        return Response(status=202)

    @app.get("/types")
    async def special_types():
        return {
            "when": dt.datetime(2026, 10, 10, 9, 30, tzinfo=dt.UTC),
            "day": dt.date(2026, 10, 10),
            "price": Decimal("75000.50"),
            "id": uuid.UUID(int=1),
            "unicode": "നമസ്കാരം",
        }

    @app.get("/bad-header")
    async def bad_header():
        return Response("x", headers={"x-evil": "a\r\nset-cookie: pwned=1"})

    @app.get("/bad-header-name")
    async def bad_header_name():
        return Response("x", headers={"bad name": "v"})

    return app


@pytest.fixture
def client(serve):
    calls.clear()
    return serve(make_app())


def test_post_json_returns_201_with_location(client):
    response = client.post("/products", json={"name": "Laptop", "price": 75000})
    assert response.status_code == 201
    assert response.headers["location"] == "/products/1"
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"name": "Laptop", "price": 75000}


def test_put_patch_delete(client):
    put = client.put("/products/3", json=[1, 2])
    assert put.json() == {"replaced": 3, "body": [1, 2]}
    assert client.patch("/products/3").json() == {"patched": 3}
    deleted = client.delete("/products/3")
    assert deleted.status_code == 204
    assert deleted.content == b""


def test_route_with_explicit_methods(client):
    assert client.get("/multi").json() == {"method": "GET"}
    assert client.post("/multi").json() == {"method": "POST"}
    response = client.put("/multi")
    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD, POST"


def test_malformed_json_is_400_and_handler_stops(client):
    response = client.post(
        "/products", content=b"{bad", headers={"content-type": "application/json"}
    )
    assert response.status_code == 400
    assert "Malformed JSON" in response.json()["error"]["message"]
    assert calls == []


def test_non_json_content_type_is_400(client):
    response = client.post("/products", content=b"name=x", headers={"content-type": "text/plain"})
    assert response.status_code == 400
    assert "application/json" in response.json()["error"]["message"]


def test_json_suffix_media_types_are_accepted(client):
    response = client.post(
        "/products", content=b'{"a":1}', headers={"content-type": "application/vnd.api+json"}
    )
    assert response.status_code == 201


def test_empty_json_body_is_400(client):
    response = client.post("/products", headers={"content-type": "application/json"})
    assert response.status_code == 400


def test_raw_body(client):
    response = client.post("/raw", content=b"x" * 1000)
    assert response.json() == {"length": 1000, "type": "bytes"}


def test_oversized_body_is_413_without_calling_handler(client):
    response = client.post("/raw", content=b"x" * (2 * 1024 * 1024))
    assert response.status_code == 413
    assert response.json()["error"]["status"] == 413
    assert calls == []


@pytest.mark.parametrize(
    ("path", "content_type", "body"),
    [
        ("/text", "text/plain; charset=utf-8", b"plain text"),
        ("/bytes", "application/octet-stream", b"\x00\x01binary"),
        ("/bytearray", "application/octet-stream", b"abc"),
        ("/tuple", "application/json", b'[1,"two"]'),
        ("/custom", "text/html; charset=utf-8", b"<h1>hi</h1>"),
    ],
)
def test_return_value_conversions(client, path, content_type, body):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"] == content_type
    assert response.content == body


def test_json_response_encodes_strings(client):
    response = client.get("/json-string")
    assert response.status_code == 202
    assert response.headers["content-type"] == "application/json"
    assert response.json() == "just a string"


def test_response_headers_and_repeated_values(client):
    response = client.get("/custom")
    assert response.headers["x-one"] == "1"
    assert response.headers.get_list("set-cookie") == ["a=1", "b=2"]


def test_response_without_content(client):
    response = client.get("/empty-response")
    assert response.status_code == 202
    assert response.content == b""


def test_special_types_are_encoded(client):
    assert client.get("/types").json() == {
        "when": "2026-10-10T09:30:00+00:00",
        "day": "2026-10-10",
        "price": "75000.50",
        "id": "00000000-0000-0000-0000-000000000001",
        "unicode": "നമസ്കാരം",
    }


@pytest.mark.parametrize("path", ["/bad-header", "/bad-header-name"])
def test_header_injection_is_refused(client, path):
    response = client.get(path)
    assert response.status_code == 500
    assert "pwned" not in response.headers.get("set-cookie", "")


def test_response_validates_status():
    with pytest.raises(ValueError, match="status"):
        Response(status=99)
    with pytest.raises(TypeError, match="header"):
        Response(headers={"x": 1})  # type: ignore[dict-item]
