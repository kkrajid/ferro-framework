# Testing

Ferro needs no special test runner: use `pytest` with `ferro.testing.TestClient` for HTTP tests
and plain `async` code for ORM tests.

```bash
pip install pytest
```

```bash
python -m pytest
```

Projects made with `ferro new` are already configured (`pyproject.toml` sets `pythonpath` and
`testpaths`) and their tests pass out of the box.

## HTTP tests with TestClient

```python
from ferro.testing import TestClient

from app import app


def test_create_and_read():
    with TestClient(app, database_url="sqlite:///:memory:") as client:
        created = client.post("/products", json={"name": "Phone", "price": "499.00"})
        assert created.status_code == 201
        product = created.json()
        assert client.get(f"/products/{product['id']}").json()["name"] == "Phone"
        assert client.get("/products/999").status_code == 404
```

- The `with` block starts your app on a free local port — the real engine, routing, limits and
  security checks, exactly as in production — and stops it at the end.
- `database_url="sqlite:///:memory:"` gives every `with` block a fresh empty database, and
  creates the tables of all registered models (`create_tables=[Model, ...]` for some only,
  `False` for none).
- `debug=True` by default, so unexpected errors include the traceback in the response body.

| Method | |
|---|---|
| `client.get/post/put/patch/delete/options(path, json=None, data=None, headers=None)` | `json=` sends JSON; `data=` sends a form; put query strings in the path |
| `response.status_code`, `.headers` (case-insensitive), `.json()`, `.text`, `.content` | |
| `client.cookies` | kept between requests (log in once, then call protected routes) |

### Logged-in tests

```python
def test_profile():
    with TestClient(app, database_url="sqlite:///:memory:") as client:
        client.post("/register", json={"username": "ann", "password": "correct horse"})
        assert client.get("/me").json()["username"] == "ann"
        client.post("/logout")
        assert client.get("/me").status_code == 401
```

To create users directly, add a route only in tests, or use the ORM pattern below before making
requests.

## ORM tests (async)

Model code is `async`. Either install `pytest-asyncio`, or add this hook to `tests/conftest.py`
(what Ferro's own test suite does):

```python
import asyncio
import inspect

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """Runs `async def` tests in a fresh event loop."""
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        names = pyfuncitem._fixtureinfo.argnames
        asyncio.run(pyfuncitem.obj(**{name: pyfuncitem.funcargs[name] for name in names}))
        return True
    return None
```

A database per test:

```python
import asyncio
import os

import pytest
from ferro import db

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:")


@pytest.fixture
def database():
    asyncio.run(db.connect(TEST_DATABASE_URL))
    yield
    asyncio.run(db.disconnect())


async def test_stock_never_negative(database):
    await db.create_tables()                       # every registered model
    phone = await Product.objects.create(name="Phone", price=Decimal("1"), stock=1)
    sold = await Product.objects.filter(pk=phone.pk, stock__gte=2).update(stock=F("stock") - 2)
    assert sold == 0
```

## Testing on PostgreSQL

Run the same tests against PostgreSQL in CI by setting `TEST_DATABASE_URL` to a dedicated,
disposable database (a `postgres` service container in CI works well). `db.create_tables()` only
creates missing tables, so clean up between tests — for example delete the rows your test
created, or drop and recreate the database per CI run. Differences between the two databases are
listed in [Models](04-models-and-queries.md#differences-between-sqlite-and-postgresql); testing
on the database you deploy to catches them.

## Migrations in CI

```bash
ferro makemigrations --check
```

fails when models changed without a migration. A typical CI job:

```bash
pip install -e . pytest
```

```bash
ferro makemigrations --check
```

```bash
python -m pytest
```

## Tips

- Each `TestClient` block is a separate server: rate limits and the memory cache start empty.
- Freeze time-dependent logic by passing times in, rather than patching the clock.
- `qs.sql()` shows the SQL a QuerySet runs — handy in assertions about query shapes.
- For load tests see [Performance and scaling](12-performance-scaling.md#load-testing).
