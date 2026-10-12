"""Security configuration: CORS and cookie defaults (Feature 006).

The checks themselves run in the native engine before a request reaches Python: the
cross-origin (CSRF) check, CORS preflights, rate limiting and security headers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ferro.exceptions import ConfigurationError

__all__ = ["CORS"]


def _strings(value: str | Sequence[str], what: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    items = tuple(value)
    for item in items:
        if not isinstance(item, str):
            raise ConfigurationError(f"CORS {what} must be strings, got {item!r}")
    return items


@dataclass(frozen=True)
class CORS:
    """Which other sites may call the API from a browser::

        App(cors=CORS(allow_origins=["https://app.example.com"], allow_credentials=True))

    ``allow_origins=["*"]`` allows every site but cannot be combined with credentials.
    Preflight requests are answered by the engine; allowed origins may also send unsafe
    requests (they pass the cross-origin check).
    """

    allow_origins: Sequence[str] = ()
    allow_methods: Sequence[str] = ("GET", "POST", "PUT", "PATCH", "DELETE")
    allow_headers: Sequence[str] = ()
    expose_headers: Sequence[str] = ()
    allow_credentials: bool = False
    max_age: int = 600

    def __post_init__(self) -> None:
        for name in ("allow_origins", "allow_methods", "allow_headers", "expose_headers"):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        if "*" in self.allow_origins and self.allow_credentials:
            raise ConfigurationError(
                "CORS allow_origins=['*'] cannot be combined with allow_credentials=True; list "
                "the allowed origins"
            )
        if isinstance(self.max_age, bool) or not isinstance(self.max_age, int):
            raise ConfigurationError("CORS max_age must be a number of seconds")
        if not 0 <= self.max_age <= 86_400:
            raise ConfigurationError("CORS max_age must be between 0 and 86400 seconds")

    def to_native(self) -> dict[str, Any]:
        return {
            "allow_origins": list(self.allow_origins),
            "allow_methods": list(self.allow_methods),
            "allow_headers": list(self.allow_headers),
            "expose_headers": list(self.expose_headers),
            "allow_credentials": self.allow_credentials,
            "max_age": self.max_age,
        }


# Cookies are `Secure` unless the running server is in development mode (set at server start).
_secure_cookies = True


def set_secure_cookie_default(secure: bool) -> None:
    global _secure_cookies
    _secure_cookies = secure


def secure_cookie_default() -> bool:
    return _secure_cookies
