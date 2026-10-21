"""Feature 026: dependency overrides, security scheme dependencies, route status codes and the
OpenAPI metadata and security they produce."""

from __future__ import annotations

import base64

import pytest
from pydantic import BaseModel

from ferro import App, ConfigurationError, Depends, Header, JSONResponse, Response
from ferro.auth import login_required
from ferro.security import APIKeyCookie, APIKeyHeader, APIKeyQuery, HTTPBasic, HTTPBearer
from ferro.testing import TestClient

# -- dependency overrides -----------------------------------------------------------------------


def settings() -> dict:
    return {"env": "production"}


async def database_name(config: dict = Depends(settings)) -> str:
    return f"db-{config['env']}"


async def current_user(token: str = Header(alias="x-token")) -> str:
    return f"user:{token}"


def override_app() -> App:
    app = App()

    @app.get("/info")
    async def info(name: str = Depends(database_name), user: str = Depends(current_user)):
        return {"name": name, "user": user}

    return app


def test_dependency_overrides_replace_nested_dependencies():
    app = override_app()
    with TestClient(app) as client:
        assert client.get("/info", headers={"x-token": "a"}).json() == {
            "name": "db-production",
            "user": "user:a",
        }
        # A nested dependency and a dependency with its own parameters, replaced at request
        # time (no re-registration); the replacement's own parameters are read as usual.
        app.dependency_overrides[settings] = lambda: {"env": "test"}

        async def fake_user(role: str = "admin") -> str:
            return f"fake:{role}"

        app.dependency_overrides[current_user] = fake_user
        assert client.get("/info?role=viewer").json() == {
            "name": "db-test",
            "user": "fake:viewer",
        }
        app.dependency_overrides.clear()
        assert client.get("/info").status_code == 422, "x-token is required again"


def test_override_dependency_context_manager_restores():
    app = override_app()
    with TestClient(app) as client:
        app.dependency_overrides[current_user] = lambda: "outer"
        with app.override_dependency(current_user, lambda: "inner"):
            assert client.get("/info").json()["user"] == "inner"
        assert client.get("/info").json()["user"] == "outer"
        with app.override_dependency(settings, lambda: {"env": "ci"}):
            assert client.get("/info").json()["name"] == "db-ci"
        assert settings not in app.dependency_overrides


def test_override_used_twice_runs_once_and_generators_clean_up():
    app = App()
    log: list[str] = []

    def real() -> str:
        return "real"

    async def replacement():
        log.append("open")
        yield "fake"
        log.append("close")

    def first(value: str = Depends(real)) -> str:
        return value

    @app.get("/twice")
    async def twice(a: str = Depends(real), b: str = Depends(first)):
        return [a, b]

    app.dependency_overrides[real] = replacement
    with TestClient(app) as client:
        assert client.get("/twice").json() == ["fake", "fake"]
    assert log == ["open", "close"]


# -- security schemes ---------------------------------------------------------------------------

bearer = HTTPBearer(bearer_format="JWT", description="Access token")
basic = HTTPBasic(realm="admin")
api_key = APIKeyHeader(name="x-api-key")
query_key = APIKeyQuery(name="key", auto_error=False)
cookie_key = APIKeyCookie(name="token", scheme_name="cookieToken")


async def token_user(token: str = Depends(bearer)) -> str:
    if token != "good":
        from ferro import HTTPException

        raise HTTPException(401, "Invalid token")
    return "alice"


def security_app() -> App:
    app = App()

    @app.get("/me")
    async def me(user: str = Depends(token_user)):
        return {"user": user}

    @app.get("/basic")
    async def basic_route(credentials: tuple = Depends(basic)):
        return {"user": credentials[0], "password": credentials[1]}

    @app.get("/key")
    async def key(value: str = Depends(api_key), optional: str | None = Depends(query_key)):
        return {"key": value, "optional": optional}

    @app.get("/cookie")
    async def cookie(value: str = Depends(cookie_key)):
        return {"cookie": value}

    @app.get("/session")
    @login_required
    async def session_route(request):
        return {"ok": True}

    return app


def test_security_schemes_read_credentials():
    with TestClient(security_app()) as client:
        missing = client.get("/me")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert client.get("/me", headers={"authorization": "Bearer bad"}).status_code == 401
        assert client.get("/me", headers={"authorization": "bearer good"}).json() == {
            "user": "alice"
        }
        assert client.get("/me", headers={"authorization": "Basic good"}).status_code == 401

        encoded = base64.b64encode(b"root:p:ss").decode()
        answer = client.get("/basic", headers={"authorization": f"Basic {encoded}"})
        assert answer.json() == {"user": "root", "password": "p:ss"}
        unauthorized = client.get("/basic", headers={"authorization": "Basic !!!"})
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == 'Basic realm="admin"'
        no_colon = base64.b64encode(b"root").decode()
        assert (
            client.get("/basic", headers={"authorization": f"Basic {no_colon}"}).status_code == 401
        )

        assert client.get("/key").status_code == 401
        assert client.get("/key", headers={"x-api-key": "k1"}).json() == {
            "key": "k1",
            "optional": None,
        }
        assert client.get("/key?key=q", headers={"x-api-key": "k1"}).json()["optional"] == "q"
        assert client.get("/cookie", headers={"cookie": "token=c1"}).json() == {"cookie": "c1"}
        assert client.get("/cookie").status_code == 401


# -- status codes and OpenAPI metadata ----------------------------------------------------------


class ItemIn(BaseModel):
    name: str


class ItemOut(BaseModel):
    id: int
    name: str


class Problem(BaseModel):
    detail: str


def test_route_status_code():
    app = App()

    @app.post("/items", status_code=201)
    async def create(item: ItemIn) -> ItemOut:
        return ItemOut(id=1, name=item.name)

    @app.post("/plain", status_code=202)
    async def plain():
        return {"queued": True}

    @app.delete("/items/{id}", status_code=204)
    async def delete(id: int):
        return None

    @app.post("/chosen", status_code=201)
    async def chosen():
        return JSONResponse({"conflict": True}, status=409)

    @app.post("/default-response", status_code=201)
    async def default_response():
        return Response("same", status=200)

    with TestClient(app) as client:
        created = client.post("/items", json={"name": "pen"})
        assert (created.status_code, created.json()) == (201, {"id": 1, "name": "pen"})
        assert client.post("/plain").status_code == 202
        deleted = client.delete("/items/3")
        assert (deleted.status_code, deleted.content) == (204, b"")
        assert client.post("/chosen").status_code == 409
        assert client.post("/default-response").status_code == 200, "a Response is kept as is"

    for bad in (99, 600, True, "201"):
        with pytest.raises(ConfigurationError, match="status_code"):
            app.get("/bad", status_code=bad)(plain)


def test_openapi_security_and_metadata():
    app = security_app()

    @app.post(
        "/items",
        status_code=201,
        summary="Create an item",
        description="Stores the item.",
        responses={409: "Already exists", 418: {"description": "Teapot", "model": Problem}},
        deprecated=True,
        operation_id="createItem",
        tags=["items"],
    )
    async def create(item: ItemIn, user: str = Depends(token_user)) -> ItemOut:
        """Ignored summary.

        Ignored description."""
        return ItemOut(id=1, name=item.name)

    app.add_openapi(title="Shop", version="2.0", servers=[{"url": "https://api.example.com"}])
    with TestClient(app) as client:
        document = client.get("/openapi.json").json()

    assert document["servers"] == [{"url": "https://api.example.com"}]
    schemes = document["components"]["securitySchemes"]
    assert schemes == {
        "HTTPBearer": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Access token",
        },
        "HTTPBasic": {"type": "http", "scheme": "basic"},
        "APIKeyHeader": {"type": "apiKey", "in": "header", "name": "x-api-key"},
        "APIKeyQuery": {"type": "apiKey", "in": "query", "name": "key"},
        "cookieToken": {"type": "apiKey", "in": "cookie", "name": "token"},
        "session": {"type": "apiKey", "in": "cookie", "name": "sessionid"},
    }
    paths = document["paths"]
    assert paths["/me"]["get"]["security"] == [{"HTTPBearer": []}], "found through a dependency"
    assert paths["/key"]["get"]["security"] == [{"APIKeyHeader": [], "APIKeyQuery": []}]
    assert paths["/session"]["get"]["security"] == [{"session": []}]
    assert "401" in paths["/me"]["get"]["responses"]
    assert "parameters" not in paths["/me"]["get"], "credentials are not query parameters"

    operation = paths["/items"]["post"]
    assert operation["operationId"] == "createItem"
    assert operation["summary"] == "Create an item"
    assert operation["description"] == "Stores the item."
    assert operation["deprecated"] is True
    assert operation["tags"] == ["items"]
    assert operation["security"] == [{"HTTPBearer": []}]
    responses = operation["responses"]
    assert set(responses) == {"201", "401", "409", "418", "422"}
    assert responses["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ItemOut"
    }
    assert responses["409"] == {"description": "Already exists"}
    assert responses["418"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Problem"
    }
    assert "Problem" in document["components"]["schemas"]
    assert "security" not in paths["/cookie"]["get"]["responses"]
    assert "/openapi.json" not in paths and "/docs" not in paths
