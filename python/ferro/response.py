"""Explicit responses. Handlers may also return dicts, lists, str, bytes or None."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any

__all__ = ["JSONResponse", "Response"]

HeadersInput = Mapping[str, str] | Iterable[tuple[str, str]] | None

_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
# RFC 6265 cookie-octet: printable ASCII except space, DQUOTE, comma, semicolon and backslash.
_COOKIE_VALUE = re.compile(r"^[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]*$")
_ATTRIBUTE = re.compile(r"^[\x20-\x3A\x3C-\x7E]*$")
_SAMESITE = {"lax": "Lax", "strict": "Strict", "none": "None"}
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _normalize_headers(headers: HeadersInput) -> list[tuple[str, str]]:
    if headers is None:
        return []
    items = headers.items() if isinstance(headers, Mapping) else headers
    normalized = []
    for item in items:
        name, value = item
        if not isinstance(name, str) or not isinstance(value, str):
            raise TypeError(f"header names and values must be str, got {name!r}: {value!r}")
        normalized.append((name, value))
    return normalized


class Response:
    """A response with explicit status, headers and media type.

    ``content`` may be ``None``, ``bytes``, ``str`` or any JSON-serializable value. When
    ``media_type`` is not given it is inferred: bytes → ``application/octet-stream``,
    str → ``text/plain; charset=utf-8``, anything else → ``application/json``.
    """

    __slots__ = ("content", "headers", "media_type", "status")

    def __init__(
        self,
        content: Any = None,
        status: int = 200,
        headers: HeadersInput = None,
        media_type: str | None = None,
    ) -> None:
        if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
            raise ValueError(f"status must be an int from 100 to 599, got {status!r}")
        self.content = content
        self.status = status
        self.headers = _normalize_headers(headers)
        self.media_type = media_type

    def set_cookie(
        self,
        name: str,
        value: str = "",
        *,
        max_age: int | None = None,
        expires: datetime | None = None,
        path: str = "/",
        domain: str | None = None,
        secure: bool | None = None,
        httponly: bool = True,
        samesite: str = "lax",
    ) -> None:
        """Adds a ``set-cookie`` header. Defaults: ``HttpOnly``, ``SameSite=Lax``, ``Path=/``,
        and ``Secure`` unless the server runs in development mode. Values must be cookie-safe
        (no spaces, quotes, commas, semicolons or backslashes); encode other data first."""
        if not isinstance(name, str) or not _TOKEN.match(name):
            raise ValueError(f"invalid cookie name {name!r}")
        if not isinstance(value, str) or not _COOKIE_VALUE.match(value):
            raise ValueError(
                f"invalid value for cookie {name!r}: use letters, digits and URL-safe symbols "
                "(encode other data, e.g. with base64)"
            )
        same_site = _SAMESITE.get(str(samesite).lower())
        if same_site is None:
            raise ValueError("samesite must be 'lax', 'strict' or 'none'")
        if secure is None:
            from ferro.security import secure_cookie_default

            secure = secure_cookie_default() or same_site == "None"
        if same_site == "None" and not secure:
            raise ValueError("samesite='none' requires secure=True (browsers reject it otherwise)")
        parts = [f"{name}={value}"]
        for label, attribute in (("Path", path), ("Domain", domain)):
            if attribute is None:
                continue
            if not _ATTRIBUTE.match(attribute):
                raise ValueError(f"invalid cookie {label.lower()} {attribute!r}")
            parts.append(f"{label}={attribute}")
        if max_age is not None:
            parts.append(f"Max-Age={int(max_age)}")
        if expires is not None:
            if expires.tzinfo is None:
                raise ValueError("cookie expires must be a timezone-aware datetime")
            parts.append(f"Expires={format_datetime(expires.astimezone(UTC), usegmt=True)}")
        if httponly:
            parts.append("HttpOnly")
        parts.append(f"SameSite={same_site}")
        if secure:
            parts.append("Secure")
        self.headers.append(("set-cookie", "; ".join(parts)))

    def delete_cookie(self, name: str, *, path: str = "/", domain: str | None = None) -> None:
        """Tells the browser to drop a cookie (same ``path``/``domain`` as when it was set)."""
        self.set_cookie(name, "", max_age=0, expires=_EPOCH, path=path, domain=domain)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(status={self.status!r}, media_type={self.media_type!r})"


class JSONResponse(Response):
    """A response whose content is always JSON-encoded (even a str)."""

    __slots__ = ()

    def __init__(
        self, content: Any = None, status: int = 200, headers: HeadersInput = None
    ) -> None:
        super().__init__(content, status, headers, "application/json")
