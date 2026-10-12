"""Feature 013 US1: select_for_update() on SQLite and PostgreSQL."""

from __future__ import annotations

import asyncio
import contextvars
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from ferro import db, models
from ferro.db import transaction
from ferro.models import Count, DatabaseUnavailableError, FieldError, TransactionError

from .conftest import use_models


class LkSupplier(models.Model):
    name = models.String(max_length=20)

    class Meta:
        table = "l13_supplier"


class LkStock(models.Model):
    sku = models.String(max_length=20, unique=True)
    quantity = models.Integer()
    supplier = models.ForeignKey(
        LkSupplier, on_delete=models.SET_NULL, null=True, related_name="stock"
    )

    class Meta:
        table = "l13_stock"


async def setup() -> tuple[LkStock, LkStock]:
    await use_models(LkSupplier, LkStock)
    acme = await LkSupplier.objects.create(name="acme")
    first = await LkStock.objects.create(sku="a", quantity=10, supplier=acme)
    second = await LkStock.objects.create(sku="b", quantity=5)
    return first, second


def outside(coroutine):
    """Runs ``coroutine`` in a task that is not part of the caller's transaction."""
    return asyncio.create_task(coroutine, context=contextvars.Context())


async def test_needs_a_transaction(database):
    first, _ = await setup()
    locked = LkStock.objects.select_for_update()
    with pytest.raises(TransactionError, match="inside a transaction"):
        await locked
    with pytest.raises(TransactionError):
        await locked.get(pk=first.pk)
    with pytest.raises(TransactionError):
        await locked.first()
    async with transaction():
        rows = await locked.order_by("sku")
        assert [s.sku for s in rows] == ["a", "b"]
        row = await locked.get(sku="a")
        row.quantity -= 1
        await row.save()
    assert (await LkStock.objects.get(sku="a")).quantity == 9


async def test_sql_per_backend(database):
    sql, _ = LkStock.objects.filter(quantity__gt=0).select_for_update(skip_locked=True).sql()
    if database == "postgres":
        assert sql.endswith('FOR UPDATE OF "l13_stock" SKIP LOCKED')
    else:
        assert "FOR UPDATE" not in sql


async def test_misuse_is_reported(database):
    await setup()
    locked = LkStock.objects.select_for_update()
    with pytest.raises(FieldError, match="together"):
        LkStock.objects.select_for_update(nowait=True, skip_locked=True)
    with pytest.raises(FieldError, match="annotate"):
        LkSupplier.objects.select_for_update().annotate(n=Count("stock"))
    with pytest.raises(FieldError, match="select_for_update"):
        LkSupplier.objects.annotate(n=Count("stock")).select_for_update()
    async with transaction():
        for call in (locked.count, locked.exists):
            with pytest.raises(FieldError, match="fetch them"):
                await call()
        with pytest.raises(FieldError, match="fetch them"):
            await locked.aggregate(total=models.Sum("quantity"))
        # values() reads rows, so it can lock them.
        assert sorted(await locked.values_list("sku", flat=True)) == ["a", "b"]


async def test_related_rows_and_nullable_joins(database):
    await setup()
    async with transaction():
        rows = await LkStock.objects.select_for_update().select_related("supplier").order_by("sku")
    assert [(s.sku, s.supplier.name if s.supplier else None) for s in rows] == [
        ("a", "acme"),
        ("b", None),
    ]


async def test_second_transaction_waits_for_the_first(database):
    first, _ = await setup()
    locked = asyncio.Event()

    async def holder() -> None:
        async with transaction():
            row = await LkStock.objects.select_for_update().get(pk=first.pk)
            locked.set()
            await asyncio.sleep(0.4)
            row.quantity = 3
            await row.save()

    async def waiter() -> tuple[int, float]:
        await locked.wait()
        started = time.monotonic()
        async with transaction():
            row = await LkStock.objects.select_for_update().get(pk=first.pk)
            return row.quantity, time.monotonic() - started

    hold = outside(holder())
    quantity, waited = await outside(waiter())
    await hold
    assert quantity == 3
    assert waited >= 0.25


async def test_nowait_and_skip_locked(database):
    if database == "sqlite":
        pytest.skip("SQLite transactions wait for the database lock when they begin")
    first, _ = await setup()
    locked = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with transaction():
            await LkStock.objects.select_for_update().get(pk=first.pk)
            locked.set()
            await release.wait()

    hold = outside(holder())
    await locked.wait()

    async def nowait() -> float:
        started = time.monotonic()
        async with transaction():
            with pytest.raises(DatabaseUnavailableError):
                await LkStock.objects.select_for_update(nowait=True).get(pk=first.pk)
        return time.monotonic() - started

    async def skip() -> list[str]:
        async with transaction():
            rows = await LkStock.objects.select_for_update(skip_locked=True).order_by("sku")
            return [s.sku for s in rows]

    try:
        assert await outside(nowait()) < 2
        assert await outside(skip()) == ["b"]
    finally:
        release.set()
        await hold


@pytest.fixture(params=["sqlite", "postgres"])
def pooled_database(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    """A database with several connections, so transactions really run side by side."""
    if request.param == "sqlite":
        db.connect_now(f"sqlite:///{(tmp_path / 'lock.db').as_posix()}", pool_size=4)
    else:
        server = request.getfixturevalue("postgres_server")
        if server is None:
            pytest.skip("PostgreSQL not available (install it or set FERRO_TEST_POSTGRES_URL)")
        schema = f"t_{uuid.uuid4().hex[:12]}"
        server.execute(f'CREATE SCHEMA "{schema}"')
        db.connect_now(server.schema_url(schema), pool_size=4)
    yield request.param
    db.disconnect_now()


async def sell(lock: bool) -> bool:
    async with transaction():
        query = LkStock.objects.select_for_update() if lock else LkStock.objects.all()
        row = await query.get(sku="a")
        if row.quantity <= 0:
            return False
        await asyncio.sleep(0.02)  # the window in which an unlocked read goes stale
        row.quantity -= 1
        await row.save()
        return True


async def test_concurrent_sales_never_oversell(pooled_database):
    """SC-001: 20 buyers, 10 items."""
    await setup()
    sold = await asyncio.gather(*(outside(sell(lock=True)) for _ in range(20)))
    assert sold.count(True) == 10
    assert (await LkStock.objects.get(sku="a")).quantity == 0

    if pooled_database == "postgres":
        # The same code without the lock loses updates.
        await LkStock.objects.filter(sku="a").update(quantity=10)
        sold = await asyncio.gather(*(outside(sell(lock=False)) for _ in range(20)))
        assert sold.count(True) > 10
