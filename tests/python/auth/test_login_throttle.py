"""Feature 023: failed logins are throttled per username and client (both databases)."""

from __future__ import annotations

import pytest

from ferro import App, HTTPException, auth
from ferro.auth import LoginThrottled, User, authenticate
from ferro.auth.sessions import configure_login_throttle
from tests.python.auth.conftest import auth_tables


def login_site(**auth_options) -> App:
    app = App()
    app.add_auth(**auth_options)

    @app.post("/login")
    async def sign_in(request):
        data = await request.json()
        user = await authenticate(data["username"], data["password"], request)
        if user is None:
            raise HTTPException(401, "Invalid credentials")
        await auth.login(request, user)
        return {"ok": True}

    return app


async def test_failed_logins_are_throttled(database, serve):
    await auth_tables()
    # Counters live in the process's cache: names differ per database run.
    name = f"ann-{database}"
    await User.objects.create_user(name, password="correct horse")
    client = serve(login_site(max_login_attempts=3, login_lockout=60), debug=True)
    wrong = {"username": name, "password": "nope"}
    assert [client.post("/login", json=wrong).status_code for _ in range(3)] == [401] * 3
    locked = client.post("/login", json={"username": name, "password": "correct horse"})
    assert locked.status_code == 429, "even the right password waits for the lockout"
    assert locked.headers["retry-after"] == "60"
    # Unknown users are counted the same way (no enumeration through the throttle).
    ghost = {"username": f"ghost-{database}", "password": "x"}
    assert [client.post("/login", json=ghost).status_code for _ in range(4)] == [
        401,
        401,
        401,
        429,
    ]
    # Another client address has its own count.
    assert await authenticate(name, "correct horse") is not None


async def test_success_resets_the_count_and_zero_disables(database):
    await auth_tables()
    name = f"bea-{database}"
    await User.objects.create_user(name, password="pw-123456")
    configure_login_throttle(2, 60)
    assert await authenticate(name, "wrong") is None
    assert await authenticate(name, "pw-123456") is not None
    assert await authenticate(name, "wrong") is None
    assert await authenticate(name, "wrong") is None
    with pytest.raises(LoginThrottled):
        await authenticate(name, "pw-123456")
    configure_login_throttle(0)
    assert await authenticate(name, "pw-123456") is not None
    with pytest.raises(ValueError):
        configure_login_throttle(-1)


async def test_database_cache_shares_counts_between_servers(database, serve):
    from ferro import db
    from ferro.cache import DatabaseCache, cache, configure
    from ferro.cache.models import CacheEntry

    await auth_tables()
    await db.create_tables([CacheEntry])
    name = f"dee-{database}"
    await User.objects.create_user(name, password="right-pass")
    previous = cache.backend
    configure(DatabaseCache())  # explicit: the servers keep it
    try:
        first = serve(login_site(max_login_attempts=2, login_lockout=60), debug=True)
        second = serve(login_site(max_login_attempts=2, login_lockout=60), debug=True)
        wrong = {"username": name, "password": "nope"}
        assert first.post("/login", json=wrong).status_code == 401
        assert second.post("/login", json=wrong).status_code == 401
        right = {"username": name, "password": "right-pass"}
        assert first.post("/login", json=right).status_code == 429, "counted across servers"
        assert await CacheEntry.objects.count() >= 1
    finally:
        cache.backend = previous
        cache.explicit = False
