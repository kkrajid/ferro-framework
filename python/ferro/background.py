"""Background tasks (Feature 025): work that runs after the handler returned, while the
response is sent — e-mails, cache warming, notifications.

```python
from ferro import BackgroundTasks


@app.post("/signup")
async def signup(data: SignupIn, tasks: BackgroundTasks):
    user = await User.objects.create_user(data.username, password=data.password)
    tasks.add_task(send_welcome_email, user.email)
    return {"id": user.pk}
```

Tasks run in the server process, in order, on the event loop (``async`` functions) or in a
worker thread (plain functions); an exception is logged and the next task still runs. A
stopping server waits for running tasks up to ``shutdown_timeout``. They are lost if the
process crashes: use a job queue for work that must not be lost.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any

__all__ = ["BackgroundTasks"]

_log = logging.getLogger("ferro.background")
_running: set[asyncio.Task[None]] = set()


class BackgroundTasks:
    """Functions to call after the response; a handler receives one by annotating a parameter
    ``BackgroundTasks``."""

    __slots__ = ("_tasks",)

    def __init__(self) -> None:
        self._tasks: list[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = []

    def add_task(self, func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> None:
        if not callable(func):
            raise TypeError(f"add_task() takes a function, got {func!r}")
        self._tasks.append((func, args, kwargs))

    def __len__(self) -> int:
        return len(self._tasks)

    def __bool__(self) -> bool:
        return bool(self._tasks)

    async def run(self) -> None:
        """Runs the tasks in order (exceptions are logged, not raised)."""
        loop = asyncio.get_running_loop()
        for func, args, kwargs in self._tasks:
            try:
                if inspect.iscoroutinefunction(func):
                    await func(*args, **kwargs)
                else:
                    context = contextvars.copy_context()
                    call = functools.partial(func, *args, **kwargs)
                    result = await loop.run_in_executor(None, context.run, call)
                    if inspect.isawaitable(result):
                        await result
            except Exception:
                _log.exception("background task %r failed", func)

    def schedule(self) -> None:
        """Starts the tasks on the running loop without waiting for them."""
        if not self._tasks:
            return
        task = asyncio.get_running_loop().create_task(self.run())
        _running.add(task)
        task.add_done_callback(_running.discard)


async def drain(timeout: float | None) -> None:
    """Waits for running background tasks (server shutdown)."""
    pending = [task for task in _running if not task.done()]
    if pending:
        await asyncio.wait(pending, timeout=timeout)
