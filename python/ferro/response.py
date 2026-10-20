"""Explicit responses. Handlers may also return dicts, lists, str, bytes or None."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Mapping
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any

__all__ = [
    "EventStream",
    "JSONResponse",
    "Response",
    "ServerSentEvent",
    "StreamingResponse",
]

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


def _async_iterator(content: Any) -> AsyncIterator[Any]:
    if hasattr(content, "__aiter__"):
        return content.__aiter__()
    if isinstance(content, (str, bytes, bytearray)) or not hasattr(content, "__iter__"):
        raise TypeError(
            "StreamingResponse takes an async iterable (an async generator) or an iterable of "
            f"str/bytes chunks, got {type(content).__name__}"
        )

    async def chunks() -> AsyncIterator[Any]:
        for chunk in content:
            yield chunk

    return chunks()


class StreamingResponse(Response):
    """A response whose body is sent piece by piece as it is produced (Feature 018)::

        async def rows():
            yield "id,name\\n"
            async for product in ...:
                yield f"{product.id},{product.name}\\n"

        return StreamingResponse(rows(), media_type="text/csv")

    ``content`` is an async iterable (or an iterable) of ``str``/``bytes`` chunks. The next chunk is
    requested when the client took the previous one. If the client disconnects or the server
    stops, the generator is closed (its ``finally`` blocks run). ``request_timeout`` covers the
    handler until it returns this object, not the stream. Keep blocking work out of the
    generator: it runs on the event loop.
    """

    __slots__ = ("_iterator",)

    def __init__(
        self,
        content: AsyncIterable[Any] | Iterable[Any],
        status: int = 200,
        headers: HeadersInput = None,
        media_type: str | None = None,
    ) -> None:
        super().__init__(None, status, headers, media_type)
        self._iterator = _async_iterator(content)


class ServerSentEvent:
    """One server-sent event: ``data`` (``str`` sent as is; other values as JSON), and optional
    ``event`` (type), ``id``, ``retry`` (milliseconds) and ``comment``."""

    __slots__ = ("comment", "data", "event", "id", "retry")

    def __init__(
        self,
        data: Any = None,
        *,
        event: str | None = None,
        id: str | int | None = None,
        retry: int | None = None,
        comment: str | None = None,
    ) -> None:
        for name, value in (("event", event), ("id", id), ("comment", comment)):
            if value is not None and any(ch in str(value) for ch in "\r\n"):
                raise ValueError(f"server-sent event {name} must not contain line breaks")
        if retry is not None and (not isinstance(retry, int) or retry < 0):
            raise ValueError("retry must be a non-negative number of milliseconds")
        self.data = data
        self.event = event
        self.id = id
        self.retry = retry
        self.comment = comment

    def encode(self) -> str:
        lines = []
        if self.comment is not None:
            lines.append(f": {self.comment}")
        if self.event is not None:
            lines.append(f"event: {self.event}")
        if self.id is not None:
            lines.append(f"id: {self.id}")
        if self.retry is not None:
            lines.append(f"retry: {self.retry}")
        if self.data is not None:
            if isinstance(self.data, str):
                text = self.data
            else:
                from ferro import _ferro

                text = _ferro.json_dumps(self.data).decode("utf-8")
            for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                lines.append(f"data: {line}")
        return "\n".join(lines) + "\n\n"

    def __repr__(self) -> str:
        return f"ServerSentEvent(data={self.data!r}, event={self.event!r}, id={self.id!r})"


async def _event_lines(events: AsyncIterator[Any], ping: float | None) -> AsyncIterator[str]:
    """Encoded events, with a keep-alive comment after ``ping`` seconds of silence."""
    pending: asyncio.Future[Any] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(events.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=ping)
            if not done:
                yield ": ping\n\n"
                continue
            future, pending = pending, None
            try:
                value = future.result()
            except StopAsyncIteration:
                return
            event = value if isinstance(value, ServerSentEvent) else ServerSentEvent(value)
            yield event.encode()
    finally:
        if pending is not None:
            # The generator cannot be closed while `__anext__` runs: let the cancellation land.
            pending.cancel()
            await asyncio.wait({pending})
            if not pending.cancelled():
                pending.exception()  # retrieved: StopAsyncIteration or the generator's error
        close = getattr(events, "aclose", None)
        if close is not None:
            await close()


class EventStream(StreamingResponse):
    """Server-sent events (``text/event-stream``)::

        @app.get("/notifications")
        async def notifications(request):
            async def events():
                async with broadcast.subscribe(f"user:{request.user.pk}") as messages:
                    async for message in messages:
                        yield ServerSentEvent(message, event="notification")
            return EventStream(events())

    Each yielded value becomes one event: a :class:`ServerSentEvent`, a ``str`` (sent as the
    data) or any JSON value. A comment line is sent after ``ping`` seconds without an event, so
    proxies and browsers keep the connection open. Sets ``cache-control: no-cache`` and
    ``x-accel-buffering: no`` (nginx).
    """

    __slots__ = ()

    def __init__(
        self,
        events: AsyncIterable[Any] | Iterable[Any],
        *,
        ping: float | None = 15.0,
        status: int = 200,
        headers: HeadersInput = None,
    ) -> None:
        merged = _normalize_headers(headers)
        present = {name.lower() for name, _ in merged}
        for name, value in (("cache-control", "no-cache"), ("x-accel-buffering", "no")):
            if name not in present:
                merged.append((name, value))
        super().__init__(
            _event_lines(_async_iterator(events), ping),
            status=status,
            headers=merged,
            media_type="text/event-stream; charset=utf-8",
        )
