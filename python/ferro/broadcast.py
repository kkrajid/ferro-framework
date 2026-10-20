"""Publish/subscribe for real-time features (Feature 018)::

    from ferro.broadcast import broadcast

    @app.websocket("/chat/{room}")
    async def chat(ws, room: str):
        async with broadcast.subscribe(f"room:{room}") as messages:
            async def forward():
                async for message in messages:
                    await ws.send_json(message)

            forwarding = asyncio.create_task(forward())
            try:
                async for text in ws:
                    await broadcast.publish(f"room:{room}", {"text": text})
            finally:
                forwarding.cancel()

Backends, chosen with the ``broadcast`` setting (``App(broadcast="database")``,
``FERRO_BROADCAST``) or :func:`configure`:

- ``"memory"`` (default): subscribers in this process only.
- ``"database"``: PostgreSQL ``LISTEN``/``NOTIFY`` — every process and machine using the same
  database receives every message. Messages are limited to 7,000 bytes of JSON.

Messages must be JSON-compatible; subscribers receive copies decoded from JSON.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from ferro import _ferro

__all__ = ["MAX_MESSAGE_BYTES", "Broadcast", "Subscription", "broadcast", "configure"]

#: Largest encoded message the database backend accepts (PostgreSQL allows 8,000 bytes).
MAX_MESSAGE_BYTES = 7000
#: Messages a subscriber may have waiting; beyond that the oldest are dropped and counted.
MAX_QUEUED = 1000
_PG_CHANNEL = "ferro_broadcast"
_BACKENDS = ("memory", "database")


class Subscription:
    """Messages published on one channel, in order, from the moment of subscribing.

    Iterate with ``async for`` or call ``await get()``. ``dropped`` counts messages discarded
    because more than 1,000 were waiting (a slow consumer)."""

    def __init__(self, channel: str) -> None:
        self.channel = channel
        self.dropped = 0
        self._queue: asyncio.Queue[Any] = asyncio.Queue()

    def _deliver(self, message: Any) -> None:
        if self._queue.qsize() >= MAX_QUEUED:
            self._queue.get_nowait()
            self.dropped += 1
        self._queue.put_nowait(message)

    async def get(self, timeout: float | None = None) -> Any:
        """The next message (``TimeoutError`` after ``timeout`` seconds)."""
        if timeout is None:
            return await self._queue.get()
        return await asyncio.wait_for(self._queue.get(), timeout)

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> Any:
        return await self._queue.get()

    def __repr__(self) -> str:
        return f"<Subscription {self.channel!r}>"


class Broadcast:
    """Delivers each published message to every subscription of its channel."""

    def __init__(self) -> None:
        self.backend = "memory"
        self.explicit = False
        self._subscriptions: dict[str, set[Subscription]] = {}
        self._listener: Any = None

    # -- publishing ------------------------------------------------------------------------------

    def _encode(self, channel: str, message: Any) -> str:
        if not isinstance(channel, str) or not channel:
            raise ValueError(f"broadcast channels are non-empty strings, got {channel!r}")
        try:
            return _ferro.json_dumps({"c": channel, "m": message}).decode("utf-8")
        except TypeError as exc:
            raise TypeError(f"broadcast message is not JSON-compatible: {exc}") from None

    async def publish(self, channel: str, message: Any) -> None:
        """Sends ``message`` to every current subscriber of ``channel``."""
        payload = self._encode(channel, message)
        if self.backend == "database":
            if len(payload.encode("utf-8")) > MAX_MESSAGE_BYTES:
                raise ValueError(
                    f"broadcast message too large for the database backend "
                    f"({len(payload.encode('utf-8'))} bytes; at most {MAX_MESSAGE_BYTES})"
                )
            await _ferro.broadcast_publish(_PG_CHANNEL, payload)
            return
        self._receive(payload)

    def _receive(self, payload: str) -> None:
        """A message for this process (published here, or a database notification)."""
        try:
            data = json.loads(payload)
            channel, message = data["c"], data["m"]
        except (ValueError, KeyError, TypeError):
            return
        for subscription in list(self._subscriptions.get(channel, ())):
            # Each subscriber gets its own copy.
            subscription._deliver(
                message
                if not isinstance(message, (dict, list))
                else json.loads(json.dumps(message))
            )

    # -- subscribing -----------------------------------------------------------------------------

    def _ensure_listener(self) -> None:
        if self.backend == "database" and self._listener is None:
            self._listener = _ferro.broadcast_listen(_PG_CHANNEL, self._receive)

    @contextlib.asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[Subscription]:
        """Receives the messages published on ``channel`` while the block runs."""
        if not isinstance(channel, str) or not channel:
            raise ValueError(f"broadcast channels are non-empty strings, got {channel!r}")
        self._ensure_listener()
        subscription = Subscription(channel)
        self._subscriptions.setdefault(channel, set()).add(subscription)
        try:
            yield subscription
        finally:
            subscribers = self._subscriptions.get(channel)
            if subscribers is not None:
                subscribers.discard(subscription)
                if not subscribers:
                    del self._subscriptions[channel]

    def subscribers(self, channel: str) -> int:
        """How many subscriptions of ``channel`` this process holds."""
        return len(self._subscriptions.get(channel, ()))

    def _stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


broadcast = Broadcast()
"""The broadcast hub of this process."""


def configure(backend: str = "memory") -> Broadcast:
    """Selects the backend: ``"memory"`` (this process) or ``"database"`` (PostgreSQL
    LISTEN/NOTIFY across processes and machines). The server then leaves the choice alone."""
    _select(backend)
    broadcast.explicit = True
    return broadcast


def _select(backend: str) -> None:
    if backend not in _BACKENDS:
        raise ValueError(f"unknown broadcast backend {backend!r}; use {', '.join(_BACKENDS)}")
    if backend != broadcast.backend:
        broadcast._stop()
        broadcast.backend = backend


def apply_setting(name: str) -> None:
    """Applies the ``broadcast`` setting when the server starts, unless the application chose
    a backend itself with :func:`configure`."""
    if not broadcast.explicit:
        _select(name)
