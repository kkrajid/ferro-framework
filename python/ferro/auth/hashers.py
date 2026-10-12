"""Password hashing and validation. Hashing (Argon2id) runs in the native core on a dedicated
thread pool, so awaiting it never blocks other requests."""

from __future__ import annotations

from ferro import _ferro
from ferro.exceptions import HTTPException

__all__ = ["PasswordError", "check_password", "make_password", "validate_password"]

MIN_LENGTH = 8


class PasswordError(HTTPException):
    """The password does not meet the rules (status 422). ``problems`` lists each one."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(422, "Password is too weak: " + "; ".join(problems))
        self.problems = problems


def validate_password(password: str, username: str | None = None) -> None:
    """Raises :class:`PasswordError` for passwords shorter than 8 characters, entirely numeric
    or equal to the username."""
    problems = []
    if not isinstance(password, str):
        raise TypeError("password must be a str")
    if len(password) < MIN_LENGTH:
        problems.append(f"it must contain at least {MIN_LENGTH} characters")
    if password.isdigit():
        problems.append("it cannot be entirely numeric")
    if username and password.strip().lower() == username.strip().lower():
        problems.append("it cannot be the same as the username")
    if problems:
        raise PasswordError(problems)


async def make_password(password: str) -> str:
    """An Argon2id hash (PHC string) of ``password``."""
    return await _ferro.hash_password(password)


async def check_password(password: str, stored: str) -> tuple[bool, bool]:
    """``(matches, needs_rehash)``; unknown hash formats never match."""
    if not isinstance(password, str) or not stored:
        return False, True
    return await _ferro.check_password(password, stored)


_dummy: str | None = None


async def dummy_hash() -> str:
    """A hash to verify against when a user does not exist, so both cases take as long."""
    global _dummy
    if _dummy is None:
        _dummy = await make_password("ferro-timing-equalizer")
    return _dummy
