"""Feature 007 US2: sessions, login and logout over HTTP (both databases)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ferro import App, HTTPException, auth
from ferro.auth import Session, User, authenticate, login_required
from ferro.models.queryset import QuerySet
from tests.python.auth.conftest import auth_tables


def site() -> App:
    app = App()
    app.add_auth()

    @app.post("/login")
    async def sign_in(request):
        data = await request.json()
        user = await authenticate(data["username"], data["password"])
        if user is None:
            raise HTTPException(401, "Invalid credentials")
        await auth.login(request, user)
        return {"ok": True}

    @app.post("/logout")
    async def sign_out(request):
        await auth.logout(request)
        return {"ok": True}

    @app.get("/me")
    @login_required
    async def me(request):
        return {"username": request.user.username, "cart": request.session.get("cart")}

    @app.post("/cart")
    async def add_to_cart(request):
        request.session["cart"] = (request.session.get("cart") or []) + ["phone"]
        return {"cart": request.session["cart"]}

    @app.get("/whoami")
    async def whoami(request):
        return {"authenticated": request.user.is_authenticated}

    return app


CREDENTIALS = {"username": "ann", "password": "correct horse"}


async def start(serve):
    await auth_tables()
    ann = await User.objects.create_user("ann", password="correct horse")
    return ann, serve(site(), debug=True)


async def test_login_sets_a_session_used_by_later_requests(database, serve):
    ann, client = await start(serve)
    assert client.get("/me").status_code == 401
    response = client.post("/login", json=CREDENTIALS)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("sessionid=") and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert client.get("/me").json() == {"username": "ann", "cart": None}
    assert (await User.objects.get(pk=ann.pk)).last_login is not None
    row = await Session.objects.get()
    key = client.cookies["sessionid"]
    assert key not in row.key_digest and len(row.key_digest) == 64, "only a digest is stored"
    assert client.post("/login", json={**CREDENTIALS, "password": "nope"}).status_code == 401


async def test_anonymous_tampered_and_expired_sessions(database, serve):
    _, client = await start(serve)
    assert client.get("/whoami").json() == {"authenticated": False}
    client.cookies.set("sessionid", "forged")
    response = client.get("/me")
    assert response.status_code == 401
    assert "sessionid=;" in response.headers.get("set-cookie", ""), "bad cookies are cleared"
    client.cookies.clear()
    client.post("/login", json=CREDENTIALS)
    await Session.objects.update(expires=datetime.now(UTC) - timedelta(seconds=1))
    assert client.get("/me").status_code == 401


async def test_logout_and_deactivation(database, serve):
    ann, client = await start(serve)
    client.post("/login", json=CREDENTIALS)
    assert client.get("/me").status_code == 200
    response = client.post("/logout")
    assert "sessionid=;" in response.headers["set-cookie"]
    assert client.get("/me").status_code == 401
    assert await Session.objects.count() == 0
    client.post("/login", json=CREDENTIALS)
    await User.objects.filter(pk=ann.pk).update(is_active=False)
    assert client.get("/me").status_code == 401


async def test_session_data_and_key_rotation(database, serve):
    _, client = await start(serve)
    assert client.post("/cart").json() == {"cart": ["phone"]}
    anonymous_key = client.cookies["sessionid"]
    assert client.post("/cart").json() == {"cart": ["phone", "phone"]}
    client.post("/login", json=CREDENTIALS)
    assert client.cookies["sessionid"] != anonymous_key, "login rotates the key"
    assert client.get("/me").json() == {"username": "ann", "cart": ["phone", "phone"]}
    assert await Session.objects.count() == 1


async def test_one_query_per_authenticated_request(database, serve, monkeypatch):
    _, client = await start(serve)
    client.post("/login", json=CREDENTIALS)
    calls: list[str] = []
    original = QuerySet._run

    async def counting(self, query):
        calls.append(self.model.__name__)
        return await original(self, query)

    monkeypatch.setattr(QuerySet, "_run", counting)
    assert client.get("/me").status_code == 200
    assert calls == ["Session"]


async def test_cookies_are_secure_in_production(database, serve):
    await auth_tables()
    await User.objects.create_user("ann", password="correct horse")
    client = serve(site(), debug=False)
    cookie = client.post("/login", json=CREDENTIALS).headers["set-cookie"]
    assert cookie.endswith("; Secure")


def test_request_user_without_auth_middleware_is_explained(serve):
    app = App()

    @app.get("/")
    async def home(request):
        try:
            _ = request.user
        except AttributeError as error:
            return {"error": str(error)}

    assert "app.add_auth()" in serve(app).get("/").json()["error"]
