"""WebSocket endpoints (Feature 018)::

    @app.websocket("/ws/chat/{room}")
    async def chat(ws, room: str):
        await ws.send_json({"joined": room})
        async for message in ws:                 # ends when the client disconnects
            await ws.send(f"echo: {message}")

The connection is accepted before the handler runs; returning closes it normally (1000), an
exception closes it with 1011 (logged with the request id). The handshake gets the same rate
limit and cross-origin check as an unsafe HTTP request.
"""

from __future__ import annotations

import json
import types
from collections.abc import Mapping
from typing import Any

from ferro import _ferro

__all__ = ["WebSocket", "WebSocketDisconnect"]


class WebSocketDisconnect(Exception):
    """The connection closed: raised by ``receive()`` and ``send()`` afterwards.

    ``code`` is the close code (1000 normal, 1001 going away, 1006 lost without a close frame,
    1009 message too big, ...); ``reason`` the close reason."""

    def __init__(self, code: int = 1000, reason: str = "") -> None:
        super().__init__(code, reason)
        self.code = code
        self.reason = reason

    def __str__(self) -> str:
        return f"WebSocket closed ({self.code}{': ' + self.reason if self.reason else ''})"


class WebSocket:
    """One WebSocket connection, given to ``@app.websocket`` handlers.

    Messages are ``str`` (text frames) or ``bytes`` (binary frames). Messages larger than
    ``max_body_size`` close the connection (1009).
    """

    __slots__ = ("_request", "_socket", "session", "state", "user")

    def __init__(self, socket: Any) -> None:
        self._socket = socket
        self._request = socket.request
        self.state = types.SimpleNamespace()
        #: The logged-in user with ``app.add_auth()`` (anonymous otherwise), else ``None``.
        self.user: Any = None
        #: The session data with ``app.add_auth()`` (read-only), else empty.
        self.session: Mapping[str, Any] = types.MappingProxyType({})

    # -- the handshake request ------------------------------------------------------------------

    @property
    def path(self) -> str:
        return self._request.path

    @property
    def raw_path(self) -> str:
        return self._request.raw_path

    @property
    def query(self) -> Any:
        return self._request.query

    @property
    def query_string(self) -> str:
        return self._request.query_string

    @property
    def path_params(self) -> dict[str, str]:
        return self._request.path_params

    @property
    def headers(self) -> Any:
        return self._request.headers

    @property
    def cookies(self) -> dict[str, str]:
        return self._request.cookies

    @property
    def client(self) -> tuple[str, int] | None:
        return self._request.client

    @property
    def request_id(self) -> str:
        return self._request.request_id

    # -- state -----------------------------------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._socket.closed

    @property
    def close_code(self) -> int | None:
        info = self._socket.close_info
        return None if info is None else info[0]

    @property
    def close_reason(self) -> str | None:
        info = self._socket.close_info
        return None if info is None else info[1]

    # -- messages --------------------------------------------------------------------------------

    async def receive(self) -> str | bytes:
        """The next message; raises :class:`WebSocketDisconnect` once the connection closed."""
        return await self._socket.receive()

    async def receive_text(self) -> str:
        message = await self.receive()
        if not isinstance(message, str):
            raise TypeError("expected a text message, got a binary one")
        return message

    async def receive_bytes(self) -> bytes:
        message = await self.receive()
        if not isinstance(message, bytes):
            raise TypeError("expected a binary message, got a text one")
        return message

    async def receive_json(self) -> Any:
        """The next message parsed as JSON (``ValueError`` when it is not JSON)."""
        return json.loads(await self.receive())

    async def send(self, data: str | bytes) -> None:
        """Sends a text (``str``) or binary (``bytes``) message. Waits only when 1 MiB is already
        queued for a slow client; raises :class:`WebSocketDisconnect` after the close."""
        await self._socket.send(data)

    async def send_json(self, value: Any) -> None:
        """Sends ``value`` as a JSON text message (same encoding as JSON responses)."""
        await self._socket.send(_ferro.json_dumps(value).decode("utf-8"))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Closes the connection: ``code`` 1000, 1001, 1003, 1007, 1008, 1011 or your own
        3000-4999; ``reason`` up to 123 bytes."""
        await self._socket.close(code, reason)

    def __aiter__(self) -> WebSocket:
        return self

    async def __anext__(self) -> str | bytes:
        try:
            return await self.receive()
        except WebSocketDisconnect:
            raise StopAsyncIteration from None

    def __repr__(self) -> str:
        return f"<WebSocket {self.path}{' closed' if self.closed else ''}>"
