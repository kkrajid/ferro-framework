"""Signals (Feature 024): let decoupled code react when something happens elsewhere.

```python
from ferro.signals import post_save, receiver


@receiver(post_save, sender=Order)
async def notify_warehouse(sender, instance, created, **kwargs):
    if created:
        await queue.publish("orders", instance.pk)
```

Receivers take ``sender`` and ``**kwargs`` (signals may add arguments later), may be ``async``
and run one after another in the order they were connected, on the event loop: keep sync
receivers quick. Prefer a direct function call when the sender and the receiver are both your
code; signals are for code that must not depend on each other (apps, plugins, auditing).

Side effects that must only happen when data is committed (e-mails, jobs, cache purges)
belong in :func:`ferro.db.on_commit`, not directly in ``post_save``.
"""

from __future__ import annotations

import inspect
import logging
import threading
import weakref
from collections.abc import Callable
from typing import Any

__all__ = [
    "Signal",
    "got_request_exception",
    "m2m_changed",
    "post_delete",
    "post_migrate",
    "post_save",
    "pre_delete",
    "pre_save",
    "receiver",
    "request_finished",
    "request_started",
    "user_logged_in",
    "user_logged_out",
    "user_login_failed",
]

_log = logging.getLogger("ferro.signals")

Receiver = Callable[..., Any]


def _identity(target: Any) -> Any:
    """A key for ``target`` that stays the same for one bound method of one object."""
    if inspect.ismethod(target):
        return (id(target.__self__), id(target.__func__))
    return id(target)


def _accepts_keywords(receiver: Receiver) -> bool:
    try:
        parameters = inspect.signature(receiver).parameters.values()
    except (TypeError, ValueError):
        return True  # builtins and C callables: trust the caller
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters)


class Signal:
    """A named event. ``connect`` receivers, then ``await signal.send(sender, **arguments)``."""

    def __init__(self, name: str = "signal") -> None:
        self.name = name
        self._receivers: list[tuple[Any, Any, Any, bool]] = []
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"<Signal {self.name}>"

    def connect(
        self,
        receiver: Receiver,
        sender: Any = None,
        weak: bool = True,
        dispatch_uid: Any = None,
    ) -> None:
        """Calls ``receiver`` on every ``send`` (only for ``sender`` when given — compared by
        identity). Weak references (the default) let a receiver that is no longer used
        elsewhere disappear: pass ``weak=False`` for local functions and lambdas. Connecting
        the same receiver (or ``dispatch_uid``) for the same sender twice keeps one."""
        if not callable(receiver):
            raise TypeError(f"{self.name}: a receiver must be callable, got {receiver!r}")
        if not _accepts_keywords(receiver):
            raise TypeError(
                f"{self.name}: receiver {getattr(receiver, '__qualname__', receiver)!r} must "
                "accept **kwargs (signals may send more arguments in later versions)"
            )
        key = (dispatch_uid if dispatch_uid is not None else _identity(receiver), _identity(sender))
        if weak:
            reference: Any = (
                weakref.WeakMethod(receiver, self._forget)
                if inspect.ismethod(receiver)
                else weakref.ref(receiver, self._forget)
            )
        else:
            reference = receiver
        with self._lock:
            if not any(existing[0] == key for existing in self._receivers):
                self._receivers.append((key, sender, reference, weak))

    def disconnect(
        self, receiver: Receiver | None = None, sender: Any = None, dispatch_uid: Any = None
    ) -> bool:
        """Removes a receiver (by ``dispatch_uid`` when it was connected with one); returns
        whether one was connected."""
        if receiver is None and dispatch_uid is None:
            raise TypeError(f"{self.name}.disconnect() needs a receiver or a dispatch_uid")
        key = (dispatch_uid if dispatch_uid is not None else _identity(receiver), _identity(sender))
        with self._lock:
            before = len(self._receivers)
            self._receivers = [entry for entry in self._receivers if entry[0] != key]
            return len(self._receivers) != before

    def _forget(self, _reference: Any) -> None:
        with self._lock:
            self._receivers = [
                entry for entry in self._receivers if not (entry[3] and entry[2]() is None)
            ]

    def has_listeners(self, sender: Any = None) -> bool:
        return bool(self._live(sender))

    def _live(self, sender: Any) -> list[Receiver]:
        if not self._receivers:
            return []
        found = []
        for _, wanted, reference, weak in list(self._receivers):
            if wanted is not None and wanted is not sender:
                continue
            target = reference() if weak else reference
            if target is not None:
                found.append(target)
        return found

    async def send(self, sender: Any, **arguments: Any) -> list[tuple[Receiver, Any]]:
        """Calls every receiver in order and returns ``[(receiver, result)]``. An exception in a
        receiver stops the others and propagates."""
        results = []
        for target in self._live(sender):
            result = target(signal=self, sender=sender, **arguments)
            if inspect.isawaitable(result):
                result = await result
            results.append((target, result))
        return results

    async def send_robust(self, sender: Any, **arguments: Any) -> list[tuple[Receiver, Any]]:
        """Like :meth:`send`, but an exception in a receiver is logged and returned as its
        result, and the remaining receivers still run."""
        results = []
        for target in self._live(sender):
            try:
                result = target(signal=self, sender=sender, **arguments)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                _log.exception("receiver %r of %s failed", target, self.name)
                result = exc
            results.append((target, result))
        return results


def receiver(signal: Signal | list[Signal] | tuple[Signal, ...], **options: Any):
    """Decorator form of :meth:`Signal.connect` (one signal or a list)::

    @receiver(post_save, sender=Order)
    async def on_order_saved(sender, instance, created, **kwargs): ...
    """

    def decorator(function: Receiver) -> Receiver:
        for each in signal if isinstance(signal, list | tuple) else (signal,):
            each.connect(function, **options)
        return function

    return decorator


# -- built-in signals --------------------------------------------------------------------------

pre_save = Signal("pre_save")
"""Before ``instance.save()`` writes: ``sender`` (model class), ``instance``, ``adding``."""

post_save = Signal("post_save")
"""After ``instance.save()`` wrote: ``sender``, ``instance``, ``created``."""

pre_delete = Signal("pre_delete")
"""Before ``instance.delete()``: ``sender``, ``instance``."""

post_delete = Signal("post_delete")
"""After ``instance.delete()``: ``sender``, ``instance`` (its key is still set)."""

m2m_changed = Signal("m2m_changed")
"""Around many-to-many changes: ``sender`` (link model), ``instance``, ``action``
(``pre_add``/``post_add``/``pre_remove``/``post_remove``/``pre_clear``/``post_clear``),
``model`` (the other side's class), ``pk_set`` (keys, ``None`` for clears), ``reverse``."""

request_started = Signal("request_started")
"""Before a routed request runs: ``sender`` (the app), ``request``."""

request_finished = Signal("request_finished")
"""After a routed request ran (also when it failed): ``sender``, ``request``."""

got_request_exception = Signal("got_request_exception")
"""A handler raised (not ``HTTPException``): ``sender``, ``request``, ``exception``."""

user_logged_in = Signal("user_logged_in")
"""After ``login()``: ``sender`` (the user's class), ``request``, ``user``."""

user_logged_out = Signal("user_logged_out")
"""After ``logout()``: ``sender``, ``request``, ``user`` (``None`` when nobody was logged in)."""

user_login_failed = Signal("user_login_failed")
"""``authenticate()`` refused: ``sender`` (``None``), ``username``, ``request``, ``throttled``."""

post_migrate = Signal("post_migrate")
"""After ``ferro migrate`` applied migrations: ``sender`` (``None``), ``applied`` (names)."""
