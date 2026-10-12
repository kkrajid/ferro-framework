"""Feature 007 US1: users and passwords."""

from __future__ import annotations

import asyncio
import statistics
import time

import pytest

from ferro.auth import PasswordError, User, authenticate, make_password
from tests.python.auth.conftest import auth_tables


async def test_create_users_and_check_passwords(database):
    await auth_tables()
    ann = await User.objects.create_user("  ann ", password="correct horse", email="a@x.io")
    assert ann.username == "ann"
    assert ann.password.startswith("$argon2id$") and "correct horse" not in ann.password
    assert await ann.check_password("correct horse")
    assert not await ann.check_password("Correct horse")
    stored = await User.objects.get(pk=ann.pk)
    assert stored.password == ann.password and stored.is_active and not stored.is_staff
    assert "password" not in stored.to_dict()
    boss = await User.objects.create_superuser("boss", "another secret", email="b@x.io")
    assert boss.is_staff and boss.is_superuser
    nopass = await User.objects.create_user("nopass")
    assert nopass.password == "" and not await nopass.check_password("")


async def test_authenticate(database):
    await auth_tables()
    await User.objects.create_user("ann", password="correct horse")
    gone = await User.objects.create_user("gone", password="correct horse", is_active=False)
    assert (await authenticate("ann", "correct horse")).username == "ann"
    assert await authenticate(" ann ", "correct horse") is not None
    assert await authenticate("ann", "wrong horse") is None
    assert await authenticate("nobody", "correct horse") is None
    assert await authenticate(gone.username, "correct horse") is None
    assert await authenticate("ann", None) is None


async def test_weak_passwords_are_refused(database):
    await auth_tables()
    for password, problem in (
        ("short", "at least 8"),
        ("1234567890", "numeric"),
        ("Charlotte", "username"),
    ):
        with pytest.raises(PasswordError, match=problem):
            await User.objects.create_user("charlotte", password=password)
    with pytest.raises(PasswordError) as info:
        await User.objects.create_user("x", password="123")
    assert len(info.value.problems) == 2 and info.value.status == 422
    assert await User.objects.count() == 0


async def test_outdated_hashes_are_upgraded_on_login(database, monkeypatch):
    await auth_tables()
    ann = await User.objects.create_user("ann", password="correct horse")
    from ferro.auth import sessions

    real = sessions.check_password

    async def outdated(password, stored):
        matches, _ = await real(password, stored)
        return matches, True

    monkeypatch.setattr(sessions, "check_password", outdated)
    assert await authenticate("ann", "correct horse") is not None
    upgraded = (await User.objects.get(pk=ann.pk)).password
    assert upgraded != ann.password and upgraded.startswith("$argon2id$")


async def test_hashing_does_not_block_the_event_loop():
    gaps: list[float] = []
    done = asyncio.Event()

    async def ticker() -> None:
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    hashes = await asyncio.gather(*(make_password(f"password {i}") for i in range(12)))
    done.set()
    await task
    assert len(set(hashes)) == 12
    assert max(gaps) < 0.05, f"event loop stalled for {max(gaps) * 1000:.0f} ms"


async def test_unknown_users_take_as_long_as_wrong_passwords(database):
    await auth_tables()
    await User.objects.create_user("ann", password="correct horse")
    await authenticate("nobody", "x")  # warm the dummy hash

    async def timed(username: str) -> float:
        started = time.perf_counter()
        await authenticate(username, "wrong password")
        return time.perf_counter() - started

    unknown = statistics.median([await timed("nobody") for _ in range(7)])
    wrong = statistics.median([await timed("ann") for _ in range(7)])
    assert abs(unknown - wrong) / max(unknown, wrong) < 0.3, (unknown, wrong)
