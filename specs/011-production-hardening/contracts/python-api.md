# Contract: Python API changes

## `DatabaseUnavailableError`

```python
from ferro.models import DatabaseUnavailableError, OperationalError
from ferro import db

issubclass(DatabaseUnavailableError, OperationalError)   # True
db.DatabaseUnavailableError is DatabaseUnavailableError   # True
```

Raised by any ORM operation, `db.connect`, `transaction()` and `create_tables` when the
database cannot be reached, the connection was lost, the database is closed, a lock wait
expired, or the database's queue is full. Not raised for SQL, constraint or missing-table
errors. Unhandled in a handler → 503 error envelope with `Retry-After: 1`; a warning is logged
on `ferro.handler` with the request id and the database's message (the client never sees it).

```python
@app.get("/products")
async def products():
    try:
        return [p.to_dict() for p in await Product.objects.all()]
    except DatabaseUnavailableError:
        return {"products": [], "stale": True}      # degrade instead of 503
```

## Request id in log records

Every `logging.LogRecord` created in the process has `record.request_id`: the id of the request
being handled by the current task or sync-handler thread, else `None`.

```python
log = logging.getLogger("shop")

@app.post("/orders")
async def create_order(request):
    log.info("creating order")          # → ... request_id=<same as the x-request-id header>
```

Applications with their own logging configuration can use `%(request_id)s` in formats.

## `ferro.log.configure(level, fmt="text")`

Internal entry point used by the server; now takes the log format. When the root logger has no
handlers (or only Ferro's), Ferro attaches its handler to it and sets its level, so application
loggers reach Ferro's output; otherwise the application's logging configuration is untouched.

## `ServerRunner`

- `begin_drain()`: readiness fails from now on; serving continues.
- `start()` raises `ConfigurationError` when a route uses a probe or metrics address.
