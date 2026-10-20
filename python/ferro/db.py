"""Database connection lifecycle for the ORM (one process-wide "default" database)."""

from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
import logging
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
    "execute",
    "fetch",
    "is_connected",
    "on_commit",
    "reset_sequences",
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


_ISOLATION_LEVELS = ("read committed", "repeatable read", "serializable")

# Callbacks waiting for the current transaction to commit: one list per open block (outermost
# first); a savepoint's list joins its parent's when it is released (Feature 024).
_pending: contextvars.ContextVar[list[list[tuple[Callable[[], Any], bool]]] | None] = (
    contextvars.ContextVar("ferro_on_commit", default=None)
)


async def _run_callback(callback: Callable[[], Any], robust: bool) -> None:
    try:
        result = callback()
        if inspect.isawaitable(result):
            await result
    except Exception:
        if not robust:
            raise
        logging.getLogger("ferro.db").exception("on_commit callback %r failed", callback)


async def on_commit(callback: Callable[[], Any], *, robust: bool = False) -> None:
    """Runs ``callback`` (sync or ``async``) after the current transaction commits — never when
    it rolls back, and not for a nested block that rolls back. Outside a transaction it runs at
    once. Use it for side effects that must only follow committed data::

        async with transaction():
            order = await Order.objects.create(...)
            await on_commit(lambda: send_confirmation(order.pk))

    With ``robust=True`` an exception is logged and the following callbacks still run;
    otherwise it propagates from the end of the ``async with`` (the data is committed).
    """
    stack = _pending.get()
    current = _ferro.transaction_context.get()
    if stack is None or current is None or current.closed:
        await _run_callback(callback, robust)
        return
    stack[-1].append((callback, robust))


async def execute(sql: str, params: Iterable[Any] = ()) -> int:
    """Runs hand-written SQL that returns no rows; returns the number of rows affected.

    ``%s`` marks each parameter (``%%`` a literal ``%``); values go in ``params``, never into
    ``sql``. Inside ``transaction()`` it joins the transaction::

        await db.execute("UPDATE product SET stock = stock - %s WHERE id = %s", [2, pid])
    """
    return await _ferro.raw_execute(sql, list(params))


async def fetch(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    """Runs a hand-written query; returns its rows as dicts (column name → value). Values come
    back in plain form (numbers, text — dates and times as ISO text, JSON as text); use
    ``Model.objects.raw()`` for model instances."""
    return await _ferro.raw_fetch(sql, list(params))


async def reset_sequences(
    models: Iterable[type[Model]] | None = None,
) -> tuple[list[str], list[str]]:
    """PostgreSQL: moves each automatic primary key sequence up to its table's highest key (for
    rows loaded by other tools with explicit ids). Never moves a sequence down. Returns
    ``(checked, moved)`` table names; on SQLite there is nothing to do.
    """
    qualnames = None
    if models is not None:
        qualnames = [model._meta.qualname for model in models]
    checked, moved = await _ferro.reset_sequences(qualnames)
    return list(checked), list(moved)


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

    ``isolation`` (``"read committed"``, ``"repeatable read"`` or ``"serializable"``) sets the
    PostgreSQL isolation level of the outermost block; a transaction the server aborts to keep
    it (serialization failure, deadlock) raises ``DatabaseUnavailableError`` — run the whole
    block again. SQLite transactions are always serializable, so the option changes nothing
    there.
    """

    def __init__(self, isolation: str | None = None) -> None:
        if isolation is not None and (
            not isinstance(isolation, str) or isolation.strip().lower() not in _ISOLATION_LEVELS
        ):
            raise ValueError(
                f"unknown isolation level {isolation!r}; use one of "
                + ", ".join(repr(level) for level in _ISOLATION_LEVELS)
            )
        self._isolation = isolation
        self._entries: list[tuple[Any, Any, int | None]] = []

    async def __aenter__(self) -> transaction:
        current = _ferro.transaction_context.get()
        if current is None or current.closed:
            session = _ferro.Session()
            try:
                await session.begin(self._isolation)
            except BaseException:
                session.close()
                raise
            token = _ferro.transaction_context.set(session)
            hooks = _pending.set([[]])
            self._entries.append((session, (token, hooks), None))
        else:
            if self._isolation is not None:
                raise TransactionError(
                    f"transaction(isolation={self._isolation!r}) is inside another "
                    "transaction(); the isolation level is set by the outermost block"
                )
            number = current.next_savepoint()
            await current.savepoint(number)
            stack = _pending.get()
            if stack is not None:
                stack.append([])
            self._entries.append((current, None, number))
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        session, tokens, savepoint = self._entries.pop()
        if savepoint is not None:
            stack = _pending.get()
            inner = stack.pop() if stack and len(stack) > 1 else []
            if exc_type is None:
                await session.release(savepoint)
                if stack:
                    stack[-1].extend(inner)
            else:
                await session.rollback_to(savepoint)
            return False
        token, hooks = tokens
        callbacks = (_pending.get() or [[]])[0]
        committed = False
        try:
            if exc_type is None:
                await session.commit()
                committed = True
            else:
                with contextlib.suppress(Exception):
                    await session.rollback()
        finally:
            _ferro.transaction_context.reset(token)
            _pending.reset(hooks)
            session.close()
        if committed:
            # Outside the finished transaction: callbacks' own queries autocommit.
            for callback, robust in callbacks:
                await _run_callback(callback, robust)
        return False

    def __call__[**P, R](self, func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(func)
        async def atomic(*args: P.args, **kwargs: P.kwargs) -> R:
            async with transaction(self._isolation):
                return await func(*args, **kwargs)

        return atomic
