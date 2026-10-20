"""Database sessions, login and logout (Feature 007 research R3).

The browser holds a random 256-bit key in the ``sessionid`` cookie; the database stores only its
SHA-256 digest. Logging in replaces the key (session fixation defence).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ferro.auth.hashers import check_password, dummy_hash, make_password
from ferro.auth.models import AnonymousUser, Session, User
from ferro.exceptions import HTTPException
from ferro.response import Response

__all__ = [
    "COOKIE_NAME",
    "SESSION_AGE",
    "SessionData",
    "auth_middleware",
    "authenticate",
    "login",
    "logout",
]

COOKIE_NAME = "sessionid"
SESSION_AGE = timedelta(days=14)
# Sliding expiry is refreshed at most this often, to avoid a write per request.
REFRESH_EVERY = timedelta(hours=1)
# A login lasts at most this long, however active the session (Feature 017).
LOGIN_MAX_AGE = timedelta(days=30)
# Session keys holding the login's time and a digest of the password hash at login: changing
# the password ends every other session of the user.
_LOGIN_AT = "_auth_login_at"
_PASSWORD = "_auth_password"


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _password_digest(user: User) -> str:
    return hashlib.sha256(f"ferro-session:{user.password}".encode()).hexdigest()[:32]


def _login_valid(data: dict[str, Any], user: User) -> bool:
    """Whether the session's login still stands: same password, not older than
    ``LOGIN_MAX_AGE``."""
    if data.get(_PASSWORD) != _password_digest(user):
        return False
    started = data.get(_LOGIN_AT)
    if not isinstance(started, (int, float)):
        return False
    return _now().timestamp() - started <= LOGIN_MAX_AGE.total_seconds()


class SessionData(dict):
    """``request.session``: a dict that remembers whether it was changed."""

    modified = False

    def _touch(self) -> None:
        self.modified = True

    def __setitem__(self, key: str, value: Any) -> None:
        super().__setitem__(key, value)
        self._touch()

    def __delitem__(self, key: str) -> None:
        super().__delitem__(key)
        self._touch()

    def pop(self, key: str, *default: Any) -> Any:
        if key in self:
            self._touch()
        return super().pop(key, *default)

    def clear(self) -> None:
        self._touch()
        super().clear()

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._touch()
        super().update(*args, **kwargs)

    def setdefault(self, key: str, default: Any = None) -> Any:
        if key not in self:
            self._touch()
        return super().setdefault(key, default)


@dataclass
class _AuthState:
    row: Session | None
    key: str | None
    action: str | None = None  # "login" | "logout"


def _auth_state(request: Any) -> _AuthState:
    state = getattr(request.state, "_ferro_auth", None)
    if state is None:
        raise RuntimeError("login()/logout() need the auth middleware: call app.add_auth()")
    return state


async def authenticate(username: str, password: str) -> User | None:
    """The active user with these credentials, or None. Takes as long for unknown users as for
    wrong passwords (both verify one hash)."""
    if not isinstance(username, str) or not isinstance(password, str):
        return None
    user = await User.objects.filter(username=username.strip()).first()
    if user is None or not user.is_active or not user.password:
        await check_password(password, await dummy_hash())
        return None
    matches, needs_rehash = await check_password(password, user.password)
    if not matches:
        return None
    if needs_rehash:
        user.password = await make_password(password)
        await User.objects.filter(pk=user.pk).update(password=user.password)
    return user


async def login(request: Any, user: User) -> None:
    """Starts a session for ``user`` (a new key, keeping the session data)."""
    if user.pk is None:
        raise ValueError("save the user before logging it in")
    auth = _auth_state(request)
    data = dict(request.session)
    data[_LOGIN_AT] = _now().timestamp()
    data[_PASSWORD] = _password_digest(user)
    if auth.row is not None:
        await Session.objects.filter(pk=auth.row.pk).delete()
    # Expired sessions are removed here, so the table only holds live ones.
    await Session.objects.filter(expires__lte=_now()).delete()
    key = secrets.token_urlsafe(32)
    auth.row = await Session.objects.create(
        key_digest=_digest(key), user=user, data=data, expires=_now() + SESSION_AGE
    )
    auth.key = key
    auth.action = "login"
    session = SessionData(data)
    request.state.session = session
    request.state.user = user
    user.last_login = _now()
    await User.objects.filter(pk=user.pk).update(last_login=user.last_login)


async def logout(request: Any) -> None:
    """Ends the session: deletes it and clears the cookie."""
    auth = _auth_state(request)
    if auth.row is not None:
        await Session.objects.filter(pk=auth.row.pk).delete()
    auth.row = None
    auth.key = None
    auth.action = "logout"
    request.state.session = SessionData()
    request.state.user = AnonymousUser()


async def read_session(cookies: Any) -> tuple[Any, Any]:
    """``(user, read-only session data)`` for a request's cookies, without saving anything:
    used for WebSocket connections (Feature 018)."""
    import types

    key = cookies.get(COOKIE_NAME)
    if not key:
        return AnonymousUser(), types.MappingProxyType({})
    row = await (
        Session.objects.select_related("user")
        .filter(key_digest=_digest(key), expires__gt=_now())
        .first()
    )
    if row is None:
        return AnonymousUser(), types.MappingProxyType({})
    user = row.user if row.user_id is not None else None
    if user is not None and (not user.is_active or not _login_valid(row.data, user)):
        user = None
    return user or AnonymousUser(), types.MappingProxyType(dict(row.data))


async def auth_middleware(request: Any, call_next: Any) -> Response:
    """Sets ``request.user`` and ``request.session``; saves sessions and cookies afterwards."""
    key = request.cookies.get(COOKIE_NAME)
    row = None
    if key:
        row = await (
            Session.objects.select_related("user")
            .filter(key_digest=_digest(key), expires__gt=_now())
            .first()
        )
    user = row.user if row is not None and row.user_id is not None else None
    if user is not None and not _login_valid(row.data, user):
        # Password changed since the login, or the login is too old: sign out.
        await Session.objects.filter(pk=row.pk).delete()
        row = None
        user = None
    request.state.user = user if user is not None and user.is_active else AnonymousUser()
    request.state.session = SessionData(row.data if row is not None else {})
    auth = _AuthState(row=row, key=key if row is not None else None)
    request.state._ferro_auth = auth

    try:
        response = await call_next(request)
    except HTTPException as error:
        # Error responses (401, 403, 422, ...) still save the session and update the cookie;
        # the cookie travels on the exception, which becomes the error response.
        carrier = Response()
        await _finish(request, auth, key, row, carrier)
        for name, value in carrier.headers:
            error.headers.setdefault(name, value)
        raise
    await _finish(request, auth, key, row, response)
    return response


async def _finish(
    request: Any, auth: _AuthState, key: str | None, row: Session | None, response: Response
) -> None:
    """Saves the session and sets, refreshes or clears the cookie on ``response``."""
    session: SessionData = request.state.session
    max_age = int(SESSION_AGE.total_seconds())
    if auth.action == "logout":
        response.delete_cookie(COOKIE_NAME)
    elif auth.action == "login":
        if session.modified:
            await Session.objects.filter(pk=auth.row.pk).update(data=dict(session))
        response.set_cookie(COOKIE_NAME, auth.key, max_age=max_age)
    elif session.modified:
        if auth.row is not None:
            await Session.objects.filter(pk=auth.row.pk).update(
                data=dict(session), expires=_now() + SESSION_AGE
            )
            response.set_cookie(COOKIE_NAME, auth.key, max_age=max_age)
        elif session:
            # Anonymous visitors get a stored session only once there is something to keep.
            new_key = secrets.token_urlsafe(32)
            await Session.objects.create(
                key_digest=_digest(new_key), data=dict(session), expires=_now() + SESSION_AGE
            )
            response.set_cookie(COOKIE_NAME, new_key, max_age=max_age)
    elif auth.row is not None and auth.row.expires - _now() < SESSION_AGE - REFRESH_EVERY:
        await Session.objects.filter(pk=auth.row.pk).update(expires=_now() + SESSION_AGE)
        response.set_cookie(COOKIE_NAME, auth.key, max_age=max_age)
    elif key and row is None:
        response.delete_cookie(COOKIE_NAME)  # unknown or expired key
