"""Feature 026: durable jobs in the database, on SQLite and PostgreSQL."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from ferro import ConfigurationError, jobs
from ferro.db import transaction
from ferro.jobs import Job

from .conftest import use_models

calls: list[tuple] = []
flaky_failures = {"left": 0}


@jobs.task(name="tests.record")
async def record(value: int, *, label: str = "x") -> None:
    calls.append(("record", value, label))


@jobs.task(name="tests.sync_record")
def sync_record(value: int) -> None:
    calls.append(("sync", value))


@jobs.task(name="tests.flaky", max_attempts=3, backoff=0)
async def flaky() -> None:
    if flaky_failures["left"] > 0:
        flaky_failures["left"] -= 1
        raise RuntimeError("temporary")
    calls.append(("flaky",))


@jobs.task(name="tests.broken", max_attempts=2, backoff=0)
async def broken() -> None:
    raise ValueError("always")


@jobs.task(name="tests.slow", timeout=0.05, max_attempts=1)
async def slow() -> None:
    await asyncio.sleep(5)


@jobs.task(name="tests.other", queue="emails")
async def other() -> None:
    calls.append(("other",))


@pytest.fixture(autouse=True)
def reset():
    calls.clear()
    flaky_failures["left"] = 0


async def burst(**options) -> int:
    return await jobs.run_worker(["default", *options.pop("extra", [])], burst=True, **options)


async def test_jobs_run_and_are_deleted(database):
    await use_models(Job)
    await record.enqueue(1, label="a")
    await jobs.enqueue(sync_record, 2)
    await jobs.enqueue("tests.record", 3)
    assert await Job.objects.count() == 3
    assert await burst(concurrency=1) == 3
    assert calls == [("record", 1, "a"), ("sync", 2), ("record", 3, "x")]
    assert await Job.objects.count() == 0, "finished jobs are deleted"
    await record(9)  # calling a task runs it now
    assert calls[-1] == ("record", 9, "x")
    with pytest.raises(LookupError):
        await jobs.enqueue("tests.missing")
    with pytest.raises(TypeError, match="JSON"):
        await record.enqueue(object())


async def test_enqueue_is_part_of_the_transaction(database):
    await use_models(Job)
    with pytest.raises(RuntimeError):
        async with transaction():
            await record.enqueue(1)
            raise RuntimeError("roll back")
    assert await Job.objects.count() == 0
    async with transaction():
        await record.enqueue(2)
    assert await Job.objects.count() == 1


async def test_retries_backoff_and_failure(database):
    await use_models(Job)
    flaky_failures["left"] = 2
    await flaky.enqueue()
    await broken.enqueue()
    for _ in range(3):
        await burst(concurrency=1)
    assert calls == [("flaky",)], "succeeded on the third attempt"
    failed = await Job.objects.get()
    assert (failed.task, failed.status, failed.attempts) == ("tests.broken", "failed", 2)
    assert "ValueError: always" in failed.last_error

    await jobs.retry(failed)
    again = await Job.objects.get()
    assert (again.status, again.attempts) == ("queued", 0)


async def test_delay_queues_timeout_and_unknown_tasks(database):
    await use_models(Job)
    await record.enqueue(1, _delay=timedelta(hours=1))
    await other.enqueue()
    await slow.enqueue()
    now = datetime.now(UTC)
    await Job.objects.create(task="tests.gone", run_at=now, created_at=now, max_attempts=1)
    await burst()
    assert calls == [], "delayed job not due; other queue not served"
    statuses = {job.task: job.status for job in await Job.objects.all()}
    assert statuses == {
        "tests.record": "queued",
        "tests.other": "queued",
        "tests.slow": "failed",
        "tests.gone": "failed",
    }
    assert "no task named 'tests.gone'" in (await Job.objects.get(task="tests.gone")).last_error
    await burst(extra=["emails"])
    assert calls == [("other",)]


async def test_an_expired_claim_is_taken_again(database):
    await use_models(Job)
    job = await record.enqueue(5)
    claimed = await jobs._claim(["default"], visibility=60)
    assert claimed is not None and claimed.pk == job.pk
    assert await jobs._claim(["default"], visibility=60) is None, "claimed jobs are hidden"
    # The worker died: its claim expires and another worker runs the job.
    await Job.objects.filter(pk=job.pk).update(
        locked_until=datetime.now(UTC) - timedelta(seconds=1)
    )
    assert await burst() == 1
    assert calls == [("record", 5, "x")]
    assert (await jobs._claim(["default"], visibility=60)) is None


async def test_concurrent_workers_run_each_job_once(database):
    await use_models(Job)
    for value in range(30):
        await record.enqueue(value)
    counts = await asyncio.gather(burst(concurrency=3), burst(concurrency=3))
    assert sum(counts) == 30
    assert sorted(call[1] for call in calls) == list(range(30))


def test_task_registration_errors():
    with pytest.raises(ConfigurationError, match="name="):

        @jobs.task
        async def nested() -> None: ...

    with pytest.raises(ConfigurationError, match="max_attempts"):
        jobs.task(name="tests.bad", max_attempts=0)(lambda: None)
    with pytest.raises(ConfigurationError, match="two tasks"):
        jobs.task(name="tests.record")(lambda: None)


WORKER_APP = """from ferro import App, jobs

app = App(database_url="sqlite:///jobs.db")


@jobs.task
def write(text):
    with open("out.txt", "a") as handle:
        handle.write(text + "\\n")
"""


def test_worker_command(tmp_path):
    (tmp_path / "app.py").write_text(WORKER_APP)
    environment = {"PYTHONPATH": str(tmp_path)}
    run = lambda *argv: subprocess.run(  # noqa: E731
        [sys.executable, "-m", "ferro", *argv],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        env={**__import__("os").environ, **environment},
    )
    created = run("db", "create-tables")
    assert created.returncode == 0, created.stderr
    assert "ferro_job" in created.stdout
    enqueue = subprocess.run(
        [
            sys.executable,
            "-c",
            "import asyncio, app\n"
            "from ferro import db\n"
            "async def main():\n"
            "    await db.connect('sqlite:///jobs.db')\n"
            "    await app.write.enqueue('one')\n"
            "    await app.write.enqueue('two')\n"
            "    await db.disconnect()\n"
            "asyncio.run(main())\n",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert enqueue.returncode == 0, enqueue.stderr
    worker = run("jobs", "worker", "--burst", "--concurrency", "1")
    assert worker.returncode == 0, worker.stderr
    assert "stopped after 2 job(s)" in worker.stderr
    assert (tmp_path / "out.txt").read_text().split() == ["one", "two"]
