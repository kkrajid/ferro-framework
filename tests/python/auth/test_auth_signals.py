"""Feature 024: user_logged_in, user_logged_out and user_login_failed (both databases)."""

from __future__ import annotations

import pytest

from ferro import App, HTTPException, auth
from ferro.auth import LoginThrottled, User, authenticate
from ferro.auth.sessions import configure_login_throttle
from ferro.signals import user_logged_in, user_logged_out, user_login_failed
from tests.python.auth.conftest import auth_tables


def site() -> App:
    app = App()
    app.add_auth()

    @app.post("/login")
    async def sign_in(request):
        data = await request.json()
        user = await authenticate(data["username"], data["password"], request)
        if user is None:
            raise HTTPException(401)
        await auth.login(request, user)
        return {"ok": True}

    @app.post("/logout")
    async def sign_out(request):
        await auth.logout(request)
        return {"ok": True}

    return app


async def test_login_signals(database, serve):
    await auth_tables()
    name = f"sig-{database}"
    await User.objects.create_user(name, password="pw-123456")
    events = []

    def logged_in(sender, request, user, **kwargs):
        events.append(("in", sender.__name__, user.username, request.path))

    def logged_out(sender, request, user, **kwargs):
        events.append(("out", user.username if user else None))

    def failed(sender, username, request, throttled, **kwargs):
        events.append(("failed", username, request is not None, throttled))

    for signal, function in (
        (user_logged_in, logged_in),
        (user_logged_out, logged_out),
        (user_login_failed, failed),
    ):
        signal.connect(function, weak=False)
    try:
        client = serve(site(), debug=True)
        assert client.post("/login", json={"username": name, "password": "bad"}).status_code == 401
        assert client.post("/login", json={"username": name, "password": "pw-123456"}).is_success
        assert client.post("/logout").is_success
        assert client.post("/logout").is_success  # nobody logged in now
        configure_login_throttle(1, 60)
        assert await authenticate("ghost-" + name, "x") is None
        with pytest.raises(LoginThrottled):
            await authenticate("ghost-" + name, "x")
    finally:
        user_logged_in.disconnect(logged_in)
        user_logged_out.disconnect(logged_out)
        user_login_failed.disconnect(failed)
    assert events == [
        ("failed", name, True, False),
        ("in", "User", name, "/login"),
        ("out", name),
        ("out", None),
        ("failed", "ghost-" + name, False, False),
        ("failed", "ghost-" + name, False, True),
    ]
