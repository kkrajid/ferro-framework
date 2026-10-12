"""Auth tests reuse the ORM fixtures, so every database test runs on SQLite and PostgreSQL."""

from __future__ import annotations

from ferro.auth import Group, Permission, Session, User
from tests.python.orm.conftest import database, postgres_server, use_models  # noqa: F401

AUTH_MODELS = (Permission, Group, User, Session)


async def auth_tables() -> None:
    await use_models(*AUTH_MODELS)
