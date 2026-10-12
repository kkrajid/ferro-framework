"""Database connection lifecycle for the ORM (one process-wide "default" database)."""

from __future__ import annotations

import contextlib
import functools
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING, Any

from ferro import _ferro
from ferro.models.exceptions import DatabaseUnavailableError, TransactionError

if TYPE_CHECKING:
    from ferro.models import Model

__all__ = [
    "DatabaseUnavailableError",
    "TransactionError",
    "connect",
    "create_tables",
    "disconnect",
    "is_connected",
    "transaction",
]


async def connect(url: str, *, pool_size: int | None = None) -> None:
    """Connects to ``url`` (``sqlite:///path.db`` or ``sqlite:///:memory:``).

    Replaces any existing connection. Raises ``ConfigurationError`` for an unsupported or
    malformed URL and ``OperationalError`` when the database cannot be opened.
    """
    connect_now(url, pool_size=pool_size)


def connect_now(url: str, *, pool_size: int | None = None) -> None:
    """Synchronous form of :func:`connect`, used at server startup."""
    _ferro.db_connect(url, pool_size)


async def disconnect() -> None:
    """Closes the database after queued queries finish."""
    _ferro.db_disconnect()


def disconnect_now() -> None:
    _ferro.db_disconnect()


def is_connected() -> bool:
    return _ferro.db_is_connected()


async def create_tables(
    models: Iterable[type[Model]] | None = None,
) -> tuple[list[str], list[str]]:
    """Creates missing tables (and their indexes) for registered models.

    Existing tables are left untouched; changing them needs migrations (Feature 004).
    Returns ``(created, existing)`` table names.
    """
    qualnames = None
    if models is not None:
        # Many-to-many link tables belong to the models that declare them.
        expanded = []
        for model in models:
            expanded.append(model)
            expanded.extend(relation.link() for relation in model._many_to_many.values())
        qualnames = list(dict.fromkeys(model._meta.qualname for model in expanded))
    registered = _ferro.registered_models(qualnames)
    existing = set(await _ferro.list_tables())
    missing = [qualname for qualname, table in registered if table not in existing]
    if missing:
        await _ferro.create_tables(missing)
    created = [table for _, table in registered if table not in existing]
    skipped = [table for _, table in registered if table in existing]
    return created, skipped


class transaction:
    """Runs ORM operations atomically::

        async with transaction():
            order = await Order.objects.create(customer=customer)
            await OrderItem.objects.create(order=order, product=phone, quantity=2)

        @transaction()
        async def place_order(...): ...

    Everything inside commits together, or nothing does when the block raises (or the task is
    cancelled). Queries of tasks started inside the block join it. Nested blocks use savepoints:
    an inner failure caught by the caller undoes only the inner block.
    """

    def __init__(self) -> None:
        self._entries: list[tuple[Any, Any, int | None]] = []

    async def __aenter__(self) -> transaction:
        current = _ferro.transaction_context.get()
        if current is None or current.closed:
            session = _ferro.Session()
            try:
                await session.begin()
            except BaseException:
                session.close()
                raise
            token = _ferro.transaction_context.set(session)
            self._entries.append((session, token, None))
        else:
            number = current.next_savepoint()
            await current.savepoint(number)
            self._entries.append((current, None, number))
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        session, token, savepoint = self._entries.pop()
        if savepoint is not None:
            if exc_type is None:
                await session.release(savepoint)
            else:
                await session.rollback_to(savepoint)
            return False
        try:
            if exc_type is None:
                await session.commit()
            else:
                with contextlib.suppress(Exception):
                    await session.rollback()
        finally:
            _ferro.transaction_context.reset(token)
            session.close()
        return False

    def __call__[**P, R](self, func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(func)
        async def atomic(*args: P.args, **kwargs: P.kwargs) -> R:
            async with transaction():
                return await func(*args, **kwargs)

        return atomic
