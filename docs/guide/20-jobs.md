# Durable background jobs

`BackgroundTasks` run in the server process after the response: fine for a log line or a cache
refresh, but a crash or a deploy loses them. For work that must happen — a receipt email, a
payment capture, a webhook — use a job: a row in the application's database that a worker runs,
retries and keeps until it succeeds.

```python
from ferro import App, Depends, jobs
from ferro.db import request_transaction

app = App(database_url="postgresql://...")


@jobs.task(max_attempts=5, timeout=30)
async def send_receipt(order_id: int) -> None:
    order = await Order.objects.get(pk=order_id)
    if order.receipt_sent:                    # jobs may run twice: make them idempotent
        return
    await mailer.send(order.email, ...)
    await Order.objects.filter(pk=order_id).update(receipt_sent=True)


@app.post("/orders")
async def place(data: OrderIn, _=Depends(request_transaction)):
    order = await Order.objects.create(...)
    await send_receipt.enqueue(order.pk)      # committed together with the order
    return order
```

```bash
ferro makemigrations && ferro migrate          # creates the ferro_job table
ferro jobs worker --concurrency 4              # as many workers, on as many machines, as you like
```

## Tasks

| | |
|---|---|
| `@jobs.task` | registers a function (`async` or sync, which runs in a thread) |
| `max_attempts=3` | runs, counting the first; then the job stays `failed` |
| `backoff=None` | seconds before a retry: a number, or `f(attempt)`; default 10 s × attempt², at most 1 h |
| `timeout=None` | a run taking longer is cancelled and counts as a failure |
| `queue="default"` | workers choose queues with `--queue` |
| `name=None` | default `module.qualname`; keep it stable while jobs are queued |
| `await task.enqueue(*args, _delay=None, _queue=None, **kwargs)` | stores a job; arguments must be JSON values |
| `await jobs.enqueue(task_or_name, ...)` | the same, by name |
| `task(...)` | calling the task runs it now, without a job |

Enqueue inside `transaction()` (or with `request_transaction`): the job then exists only if
the transaction commits, so a worker never sees an order that was rolled back, and an order is
never committed without its job.

## Workers

`ferro jobs worker [module:app]` imports the app (registering its tasks), connects to its
database, runs its startup hooks and then runs due jobs, `--concurrency` at a time, polling
every `--poll-interval` seconds when idle. Ctrl+C or SIGTERM lets running jobs finish, then
runs the shutdown hooks. `--burst` exits when no job is due (cron, tests).

Delivery is **at least once**. A worker claims a job for `--visibility-timeout` seconds
(default 300) and renews the claim while the job runs; if the worker dies, the claim expires
and another worker runs the job again. On PostgreSQL workers skip each other's locked rows
(`SELECT ... FOR UPDATE SKIP LOCKED`); on SQLite one write transaction claims at a time. A
claim is also a conditional update, so two workers never run one claim.

A job that succeeds is deleted. A job that fails for good keeps `status="failed"`, its
attempts and its last traceback (`last_error`) in `ferro_job`:

```python
from ferro.jobs import Job, retry

for job in await Job.objects.filter(status="failed"):
    print(job.task, job.args, job.last_error)
    await retry(job)                           # back in the queue, attempts reset
```

The queue lives in your database: no broker to run, and it is transactional with your data.
Its throughput is that of your database (a few hundred jobs per second on PostgreSQL); for
much more, use a dedicated queue.
