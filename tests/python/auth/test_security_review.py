"""Feature 017: regressions for the security review's session and password findings (both
databases)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ferro import HTTPException
from ferro.auth import Session, make_password
from ferro.auth.sessions import LOGIN_MAX_AGE, SessionData
from tests.python.auth.test_sessions import CREDENTIALS, start


def test_popping_a_missing_key_changes_nothing():
    data = SessionData()
    assert data.pop("missing", None) is None
    assert not data.modified
    data["k"] = 1
    data.modified = False
    data.pop("k")
    assert data.modified


async def test_anonymous_requests_store_no_sessions(database, serve):
    _, client = await start(serve)
    for _ in range(5):
        assert client.get("/whoami").json() == {"authenticated": False}
    response = client.post("/login", json={"username": "ann", "password": "wrong"})
    assert response.status_code == 401
    assert await Session.objects.count() == 0
    assert "sessionid" not in client.cookies


async def test_changing_the_password_ends_other_sessions(database, serve):
    ann, client = await start(serve)
    client.post("/login", json=CREDENTIALS)
    assert client.get("/me").status_code == 200
    await ann.set_password("a brand new secret")
    await ann.save()
    assert client.get("/me").status_code == 401
    assert await Session.objects.count() == 0, "the stale session is deleted"


async def test_logins_have_an_absolute_lifetime(database, serve):
    _, client = await start(serve)
    client.post("/login", json=CREDENTIALS)
    row = await Session.objects.first()
    old = datetime.now(UTC) - LOGIN_MAX_AGE - timedelta(minutes=1)
    await Session.objects.filter(pk=row.pk).update(
        data={**row.data, "_auth_login_at": old.timestamp()}
    )
    assert client.get("/me").status_code == 401


async def test_expired_sessions_are_removed_at_login(database, serve):
    _, client = await start(serve)
    client.post("/login", json=CREDENTIALS)
    await Session.objects.update(expires=datetime.now(UTC) - timedelta(days=1))
    client.cookies.clear()
    client.post("/login", json=CREDENTIALS)
    assert await Session.objects.count() == 1


async def test_password_hashing_floods_get_503():
    results = await asyncio.gather(
        *(make_password("load test password") for _ in range(400)), return_exceptions=True
    )
    busy = [r for r in results if isinstance(r, HTTPException)]
    hashes = [r for r in results if isinstance(r, str)]
    assert busy, "a full queue answers instead of growing"
    assert all(e.status == 503 and e.headers.get("retry-after") == "1" for e in busy)
    assert hashes and all(h.startswith("$argon2id$") for h in hashes)
    assert len(busy) + len(hashes) == 400
