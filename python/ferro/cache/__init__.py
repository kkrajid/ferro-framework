"""A cache for expensive results (Feature 015)::

    from ferro.cache import cache

    page = await cache.get_or_set("home", build_home, timeout=60)
    await cache.set("rates", {"usd": 83.1}, timeout=300)
    await cache.incr("visits")

Two backends, chosen with the ``cache`` setting (``App(cache="database")``, ``FERRO_CACHE``) or
:func:`configure`:

- ``"memory"`` (default): per process, least recently used entries evicted beyond
  ``max_entries``. With ``ferro run --processes N`` each process has its own.
- ``"database"``: the ``ferro_cache`` table, shared by every process and server using the
  database. ``App(cache="database")`` makes ``makemigrations`` create the table.

Values must be JSON-compatible (``dict``, ``list``, ``str``, numbers, ``bool``, ``None``); they
come back as JSON values (tuples become lists). ``timeout`` is in seconds: ``None`` never
expires, ``0`` or less does not store. Keys are strings of 1 to 250 characters.
"""

from __future__ import annotations

import inspect
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from ferro import _ferro

__all__ = [
    "DEFAULT_TIMEOUT",
    "BaseCache",
    "DatabaseCache",
    "MemoryCache",
    "cache",
    "configure",
]

DEFAULT_TIMEOUT: float | None = 300.0
"""Seconds an entry lives when ``set()`` gets no ``timeout``."""

_MISSING: Any = object()
MAX_KEY_LENGTH = 250


def _check_key(key: Any) -> str:
    if not isinstance(key, str) or not key:
        raise ValueError(f"cache keys are non-empty strings, got {key!r}")
    if len(key) > MAX_KEY_LENGTH:
        raise ValueError(f"cache key longer than {MAX_KEY_LENGTH} characters: {key[:40]!r}...")
    return key


def _dump(key: str, value: Any) -> str:
    try:
        return _ferro.json_dumps(value).decode("utf-8")
    except TypeError as exc:
        raise TypeError(f"cache value for {key!r} is not JSON-compatible: {exc}") from None


def _load(text: str) -> Any:
    return _ferro.json_loads(text.encode("utf-8"))


def _expiry(timeout: float | None) -> float | None:
    """Absolute expiry time; ``None`` never expires."""
    return None if timeout is None else time.time() + timeout


class BaseCache:
    """The cache API; backends implement the underscored methods."""

    default_timeout: float | None = DEFAULT_TIMEOUT

    def _timeout(self, timeout: Any) -> float | None:
        if timeout is _MISSING:
            return self.default_timeout
        if timeout is None:
            return None
        return float(timeout)

    async def get(self, key: str, default: Any = None) -> Any:
        """The value stored under ``key``, or ``default`` when missing or expired."""
        found = await self._get(_check_key(key))
        return default if found is _MISSING else found

    async def set(self, key: str, value: Any, timeout: Any = _MISSING) -> None:
        """Stores ``value``, replacing any existing one."""
        key, timeout = _check_key(key), self._timeout(timeout)
        text = _dump(key, value)
        if timeout is not None and timeout <= 0:
            await self._delete(key)
            return
        await self._set(key, text, _expiry(timeout))

    async def add(self, key: str, value: Any, timeout: Any = _MISSING) -> bool:
        """Stores ``value`` only if ``key`` is missing or expired; returns whether it did."""
        key, timeout = _check_key(key), self._timeout(timeout)
        text = _dump(key, value)
        if timeout is not None and timeout <= 0:
            return False
        return await self._add(key, text, _expiry(timeout))

    async def get_or_set(
        self,
        key: str,
        default: Any | Callable[[], Any] | Callable[[], Awaitable[Any]],
        timeout: Any = _MISSING,
    ) -> Any:
        """The cached value, or ``default`` (called, and awaited if it returns an awaitable,
        when callable) stored and returned on a miss."""
        found = await self._get(_check_key(key))
        if found is not _MISSING:
            return found
        value = default() if callable(default) else default
        if inspect.isawaitable(value):
            value = await value
        if await self.add(key, value, timeout):
            return value
        # Another task or process stored it first: use theirs.
        found = await self._get(key)
        return value if found is _MISSING else found

    async def delete(self, key: str) -> bool:
        """Removes ``key``; returns whether it was there."""
        return await self._delete(_check_key(key))

    async def has_key(self, key: str) -> bool:
        return await self._get(_check_key(key)) is not _MISSING

    async def incr(self, key: str, delta: int = 1) -> int:
        """Adds ``delta`` to an integer value atomically and returns the result; ``ValueError``
        when the key is missing or not an integer."""
        if not isinstance(delta, int) or isinstance(delta, bool):
            raise TypeError(f"incr() delta must be an int, got {delta!r}")
        return await self._incr(_check_key(key), delta)

    async def decr(self, key: str, delta: int = 1) -> int:
        return await self.incr(key, -delta)

    async def get_many(self, keys: Iterable[str]) -> dict[str, Any]:
        """Values of the keys that are present."""
        found = {}
        for key in keys:
            value = await self._get(_check_key(key))
            if value is not _MISSING:
                found[key] = value
        return found

    async def set_many(self, values: Mapping[str, Any], timeout: Any = _MISSING) -> None:
        for key, value in values.items():
            await self.set(key, value, timeout)

    async def delete_many(self, keys: Iterable[str]) -> None:
        for key in keys:
            await self._delete(_check_key(key))

    async def clear(self) -> None:
        """Removes every entry."""
        await self._clear()

    async def _get(self, key: str) -> Any:
        raise NotImplementedError

    async def _set(self, key: str, text: str, expires: float | None) -> None:
        raise NotImplementedError

    async def _add(self, key: str, text: str, expires: float | None) -> bool:
        raise NotImplementedError

    async def _delete(self, key: str) -> bool:
        raise NotImplementedError

    async def _incr(self, key: str, delta: int) -> int:
        raise NotImplementedError

    async def _clear(self) -> None:
        raise NotImplementedError


def _integer(key: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"cache value for {key!r} is not an integer")
    return value


class MemoryCache(BaseCache):
    """Entries in this process only, least recently used evicted beyond ``max_entries``."""

    def __init__(self, max_entries: int = 10_000, default_timeout: Any = _MISSING) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self.max_entries = max_entries
        if default_timeout is not _MISSING:
            self.default_timeout = default_timeout
        self._entries: OrderedDict[str, tuple[str, float | None]] = OrderedDict()

    def _live(self, key: str) -> tuple[str, float | None] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry[1] is not None and entry[1] <= time.time():
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return entry

    def _store(self, key: str, text: str, expires: float | None) -> None:
        self._entries[key] = (text, expires)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    async def _get(self, key: str) -> Any:
        entry = self._live(key)
        return _MISSING if entry is None else _load(entry[0])

    async def _set(self, key: str, text: str, expires: float | None) -> None:
        self._store(key, text, expires)

    async def _add(self, key: str, text: str, expires: float | None) -> bool:
        if self._live(key) is not None:
            return False
        self._store(key, text, expires)
        return True

    async def _delete(self, key: str) -> bool:
        return self._entries.pop(key, None) is not None

    async def _incr(self, key: str, delta: int) -> int:
        entry = self._live(key)
        if entry is None:
            raise ValueError(f"cache key {key!r} not found")
        value = _integer(key, _load(entry[0])) + delta
        self._entries[key] = (_dump(key, value), entry[1])
        return value

    async def _clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


class DatabaseCache(BaseCache):
    """Entries in the ``ferro_cache`` table, shared by every process using the database."""

    CULL_EVERY = 100

    def __init__(self, default_timeout: Any = _MISSING) -> None:
        from ferro.cache.models import CacheEntry

        self.model = CacheEntry
        if default_timeout is not _MISSING:
            self.default_timeout = default_timeout
        self._writes = 0

    async def _get(self, key: str) -> Any:
        entry = await self.model.objects.filter(key=key).first()
        if entry is None:
            return _MISSING
        if entry.expires is not None and entry.expires <= time.time():
            await self.model.objects.filter(key=key, expires__lte=time.time()).delete()
            return _MISSING
        return _load(entry.value)

    async def _written(self) -> None:
        self._writes += 1
        if self._writes % self.CULL_EVERY == 0:
            await self.model.objects.filter(expires__lte=time.time()).delete()

    async def _set(self, key: str, text: str, expires: float | None) -> None:
        from ferro.models import IntegrityError

        objects = self.model.objects
        if not await objects.filter(key=key).update(value=text, expires=expires):
            try:
                await objects.create(key=key, value=text, expires=expires)
            except IntegrityError:
                # Created by someone else in between: overwrite theirs, as set() does.
                await objects.filter(key=key).update(value=text, expires=expires)
        await self._written()

    async def _add(self, key: str, text: str, expires: float | None) -> bool:
        from ferro.models import IntegrityError

        objects = self.model.objects
        try:
            await objects.create(key=key, value=text, expires=expires)
        except IntegrityError:
            # Present: replace it only if it has expired (one statement, so a live value
            # written meanwhile by another process is never replaced).
            replaced = await objects.filter(key=key, expires__lte=time.time()).update(
                value=text, expires=expires
            )
            if not replaced:
                return False
        await self._written()
        return True

    async def _delete(self, key: str) -> bool:
        return bool(await self.model.objects.filter(key=key).delete())

    async def _incr(self, key: str, delta: int) -> int:
        from ferro.db import transaction

        async with transaction():
            entry = await self.model.objects.select_for_update().filter(key=key).first()
            if entry is None or (entry.expires is not None and entry.expires <= time.time()):
                raise ValueError(f"cache key {key!r} not found")
            value = _integer(key, _load(entry.value)) + delta
            await self.model.objects.filter(key=key).update(value=_dump(key, value))
        return value

    async def _clear(self) -> None:
        await self.model.objects.all().delete()


BACKENDS: dict[str, Callable[[], BaseCache]] = {
    "memory": MemoryCache,
    "database": DatabaseCache,
}


class _Cache(BaseCache):
    """``ferro.cache.cache``: forwards to the configured backend (memory until configured)."""

    def __init__(self) -> None:
        self.backend: BaseCache = MemoryCache()
        self.explicit = False

    @property
    def default_timeout(self) -> float | None:  # type: ignore[override]
        return self.backend.default_timeout

    async def _get(self, key: str) -> Any:
        return await self.backend._get(key)

    async def _set(self, key: str, text: str, expires: float | None) -> None:
        await self.backend._set(key, text, expires)

    async def _add(self, key: str, text: str, expires: float | None) -> bool:
        return await self.backend._add(key, text, expires)

    async def _delete(self, key: str) -> bool:
        return await self.backend._delete(key)

    async def _incr(self, key: str, delta: int) -> int:
        return await self.backend._incr(key, delta)

    async def _clear(self) -> None:
        await self.backend._clear()

    def __repr__(self) -> str:
        return f"<cache {type(self.backend).__name__}>"


cache = _Cache()
"""The cache configured for this process."""


def configure(backend: str | BaseCache = "memory", **options: Any) -> BaseCache:
    """Selects the backend of :data:`cache`: ``"memory"`` (options: ``max_entries``,
    ``default_timeout``), ``"database"`` (``default_timeout``) or a :class:`BaseCache`
    instance. Returns the backend."""
    if isinstance(backend, BaseCache):
        if options:
            raise TypeError("options are only accepted with a backend name")
        chosen = backend
    else:
        factory = BACKENDS.get(backend)
        if factory is None:
            raise ValueError(f"unknown cache backend {backend!r}; use {', '.join(BACKENDS)}")
        chosen = factory(**options)
    cache.backend = chosen
    cache.explicit = True
    return chosen


def apply_setting(name: str) -> None:
    """Applies the ``cache`` setting when the server starts, unless the application chose a
    backend itself with :func:`configure`."""
    if not cache.explicit and not isinstance(cache.backend, BACKENDS[name]):
        cache.backend = BACKENDS[name]()
