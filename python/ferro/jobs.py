"""Durable background jobs (Feature 026): a queue in the application's database.

``BackgroundTasks`` run in the server process after the response; a crash or a deploy loses
them. A job is a row in ``ferro_job``: it survives restarts, is retried with backoff when it
fails, and is enqueued in the caller's transaction — it exists only if the transaction
commits::

    from ferro import jobs


    @jobs.task(max_attempts=5)
    async def send_receipt(order_id: int) -> None:
        order = await Order.objects.get(pk=order_id)
        await mailer.send(order.email, ...)


    @app.post("/orders")
    async def place(data: OrderIn, _=Depends(request_transaction)):
        order = await Order.objects.create(...)
        await send_receipt.enqueue(order.pk)  # committed with the order

Run workers with ``ferro jobs worker`` (as many as you like, on any machines sharing the
database). Delivery is *at least once*: a worker that dies mid-job leaves it locked until its
visibility timeout passes, and then another worker runs it again — make jobs idempotent (check
whether the receipt was already sent). Arguments are stored as JSON. A job that succeeds is
deleted; one that fails ``max_attempts`` times stays with ``status="failed"`` and its last
error, for you to inspect and :func:`retry`.

The table is a normal model: ``ferro makemigrations`` (or ``ferro db create-tables``) creates it
once ``ferro.jobs`` is imported.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import traceback
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from ferro import models
from ferro.exceptions import ConfigurationError

__all__ = ["Job", "Task", "enqueue", "retry", "run_worker", "task"]

log = logging.getLogger("ferro.jobs")

QUEUED = "queued"
RUNNING = "running"
FAILED = "failed"


class Job(models.Model):
    """One enqueued call of a :func:`task`."""

    task = models.String(max_length=200)
    queue = models.String(max_length=50, default="default")
    args = models.JSON(default=list)
    kwargs = models.JSON(default=dict)
    status = models.String(max_length=10, default=QUEUED, index=True)
    attempts = models.Integer(default=0)
    max_attempts = models.Integer(default=3)
    #: When the job may run next (``enqueue(delay=...)``, retry backoff).
    run_at = models.DateTime(index=True)
    #: While running: when the worker's claim expires unless it renews it.
    locked_until = models.DateTime(null=True)
    last_error = models.Text(null=True)
    created_at = models.DateTime()

    class Meta:
        table = "ferro_job"

    def __repr__(self) -> str:
        return f"<Job {self.pk} {self.task} {self.status} attempt {self.attempts}>"


def _now() -> datetime:
    return datetime.now(UTC)


def _default_backoff(attempt: int) -> float:
    """Seconds before retry ``attempt`` (1, 2, ...): 10 s, 40 s, 90 s, ... at most an hour."""
    return min(10.0 * attempt * attempt, 3600.0)


_TASKS: dict[str, Task] = {}


class Task:
    """A function that can run as a job (made by :func:`task`). Calling it runs it now;
    ``await enqueue(...)`` stores a job for a worker."""

    def __init__(
        self,
        function: Callable[..., Any],
        *,
        name: str,
        queue: str,
        max_attempts: int,
        backoff: Callable[[int], float],
        timeout: float | None,
    ) -> None:
        self.function = function
        self.name = name
        self.queue = queue
        self.max_attempts = max_attempts
        self.backoff = backoff
        self.timeout = timeout
        self.__doc__ = function.__doc__
        self.__wrapped__ = function

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.function(*args, **kwargs)

    async def enqueue(
        self,
        *args: Any,
        _delay: float | timedelta | None = None,
        _queue: str | None = None,
        **kwargs: Any,
    ) -> Job:
        """Stores a job calling this task with ``args`` and ``kwargs`` (JSON values). Inside
        ``transaction()`` it is part of the transaction. ``_delay`` postpones it."""
        try:
            json.dumps([args, kwargs])
        except (TypeError, ValueError) as exc:
            raise TypeError(f"job arguments of {self.name} must be JSON values: {exc}") from None
        if isinstance(_delay, timedelta):
            _delay = _delay.total_seconds()
        now = _now()
        return await Job.objects.create(
            task=self.name,
            queue=_queue or self.queue,
            args=list(args),
            kwargs=kwargs,
            max_attempts=self.max_attempts,
            run_at=now + timedelta(seconds=_delay or 0),
            created_at=now,
        )

    def __repr__(self) -> str:
        return f"<Task {self.name}>"


def task(
    function: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    queue: str = "default",
    max_attempts: int = 3,
    backoff: float | Callable[[int], float] | None = None,
    timeout: float | None = None,
) -> Any:
    """Registers a function (sync or ``async``) as a job task. ``max_attempts`` counts the
    first run; ``backoff`` is the seconds before a retry (a number, or a function of the attempt
    number); ``timeout`` cancels a run that takes longer (it then counts as a failure).
    ``name`` defaults to ``module.qualname`` — keep it stable while jobs are queued."""

    def register(function: Callable[..., Any]) -> Task:
        if not callable(function):
            raise TypeError("task() takes a function")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise ConfigurationError(f"max_attempts must be a positive int, got {max_attempts!r}")
        label = name or f"{function.__module__}.{function.__qualname__}"
        if "<locals>" in label and name is None:
            raise ConfigurationError(
                f"task {label}: a nested function needs name=... (workers import it by name)"
            )
        existing = _TASKS.get(label)
        if existing is not None and existing.function is not function:
            raise ConfigurationError(f"two tasks are named {label!r}")
        if backoff is None:
            delay = _default_backoff
        elif callable(backoff):
            delay = backoff
        else:
            seconds = float(backoff)
            delay = lambda attempt: seconds  # noqa: E731
        made = Task(
            function,
            name=label,
            queue=queue,
            max_attempts=max_attempts,
            backoff=delay,
            timeout=timeout,
        )
        _TASKS[label] = made
        return made

    return register if function is None else register(function)


async def enqueue(target: Task | str, *args: Any, **kwargs: Any) -> Job:
    """``await enqueue(send_receipt, 7)``: the same as ``send_receipt.enqueue(7)``; a task may
    also be named by its registered name."""
    found = _TASKS.get(target) if isinstance(target, str) else target
    if found is None:
        raise LookupError(f"no task named {target!r}")
    return await found.enqueue(*args, **kwargs)


async def retry(job: Job | int, *, reset_attempts: bool = True) -> None:
    """Puts a failed job back in the queue, to run now."""
    pk = job.pk if isinstance(job, Job) else job
    values: dict[str, Any] = {"status": QUEUED, "run_at": _now(), "locked_until": None}
    if reset_attempts:
        values["attempts"] = 0
    await Job.objects.filter(pk=pk).update(**values)


# -- the worker ----------------------------------------------------------------------------------


async def _claim(queues: list[str], visibility: float) -> Job | None:
    """Takes the next due job: queued and due, or running with an expired claim (its worker
    died). The conditional update makes two workers racing for one job safe on any backend;
    ``skip_locked`` keeps PostgreSQL workers from waiting on each other."""
    from ferro.db import transaction

    now = _now()
    due = models.Q(status=QUEUED, run_at__lte=now) | models.Q(status=RUNNING, locked_until__lt=now)
    async with transaction():
        job = await (
            Job.objects.select_for_update(skip_locked=True)
            .filter(due, queue__in=queues)
            .order_by("run_at", "pk")
            .first()
        )
        if job is None:
            return None
        claimed = await Job.objects.filter(
            pk=job.pk, status=job.status, attempts=job.attempts
        ).update(
            status=RUNNING,
            attempts=job.attempts + 1,
            locked_until=now + timedelta(seconds=visibility),
        )
    if not claimed:
        return None
    job.status, job.attempts = RUNNING, job.attempts + 1
    return job


async def _renew(job: Job, visibility: float) -> None:
    """Extends the claim while the job runs, so a long job is not taken by another worker."""
    while True:
        await asyncio.sleep(visibility / 3)
        await Job.objects.filter(pk=job.pk, status=RUNNING, attempts=job.attempts).update(
            locked_until=_now() + timedelta(seconds=visibility)
        )


async def _call(found: Task, job: Job) -> None:
    if inspect.iscoroutinefunction(found.function):
        work: Any = found.function(*job.args, **job.kwargs)
    else:
        work = asyncio.to_thread(found.function, *job.args, **job.kwargs)
    if found.timeout is None:
        await work
    else:
        await asyncio.wait_for(work, found.timeout)


async def run_job(job: Job, visibility: float = 300.0) -> bool:
    """Runs a claimed job and records the outcome; ``True`` when it succeeded."""
    found = _TASKS.get(job.task)
    renewal = asyncio.create_task(_renew(job, visibility))
    try:
        if found is None:
            raise LookupError(f"no task named {job.task!r} (is its module imported?)")
        await _call(found, job)
    except Exception:
        error = traceback.format_exc()[-8000:]
        renewal.cancel()
        if found is not None and job.attempts < job.max_attempts:
            delay = found.backoff(job.attempts)
            log.warning(
                "job %s %s failed (attempt %s of %s), retrying in %.0f s",
                job.pk,
                job.task,
                job.attempts,
                job.max_attempts,
                delay,
            )
            values: dict[str, Any] = {
                "status": QUEUED,
                "run_at": _now() + timedelta(seconds=delay),
            }
        else:
            log.error("job %s %s failed for good:\n%s", job.pk, job.task, error)
            values = {"status": FAILED}
        await Job.objects.filter(pk=job.pk, attempts=job.attempts).update(
            locked_until=None, last_error=error, **values
        )
        return False
    finally:
        renewal.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await renewal
    await Job.objects.filter(pk=job.pk, attempts=job.attempts).delete()
    return True


async def run_worker(
    queues: Iterable[str] = ("default",),
    *,
    concurrency: int = 4,
    poll_interval: float = 1.0,
    visibility_timeout: float = 300.0,
    burst: bool = False,
    stop: asyncio.Event | None = None,
) -> int:
    """Runs jobs from ``queues`` with ``concurrency`` at a time until ``stop`` is set (or, with
    ``burst=True``, until no job is due). Returns the number of jobs run. The database must be
    connected."""
    if concurrency < 1:
        raise ConfigurationError("concurrency must be at least 1")
    names = list(queues)
    stop = stop or asyncio.Event()
    done = 0

    async def loop() -> None:
        nonlocal done
        while not stop.is_set():
            try:
                job = await _claim(names, visibility_timeout)
            except Exception:
                log.exception("could not claim a job; retrying")
                job = None
            if job is None:
                if burst:
                    return
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), poll_interval)
                continue
            await run_job(job, visibility_timeout)
            done += 1

    await asyncio.gather(*(loop() for _ in range(concurrency)))
    return done
