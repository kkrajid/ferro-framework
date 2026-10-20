"""Feature 005 US2: transactions on SQLite and PostgreSQL."""

from __future__ import annotations

import asyncio

import pytest

from ferro import models
from ferro.db import transaction
from ferro.models import F, IntegrityError, OperationalError

from .conftest import use_models


class Account(models.Model):
    owner = models.String(max_length=30, unique=True)
    balance = models.Integer(default=0)

    class Meta:
        table = "t5_account"


class Entry(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="entries")
    amount = models.Integer()

    class Meta:
        table = "t5_entry"


class Boom(Exception):
    pass


async def setup():
    await use_models(Account, Entry)
    return await Account.objects.create(owner="ann", balance=100)


async def balances() -> dict[str, int]:
    return {a.owner: a.balance for a in await Account.objects.all()}


async def test_commit_and_rollback(database):
    ann = await setup()
    async with transaction():
        await Account.objects.create(owner="bob", balance=5)
        await Entry.objects.create(account=ann, amount=-10)
        await Account.objects.filter(pk=ann.pk).update(balance=F("balance") - 10)
    assert await balances() == {"ann": 90, "bob": 5}

    with pytest.raises(Boom):
        async with transaction():
            await Account.objects.create(owner="cid")
            await Entry.objects.create(account=ann, amount=-50)
            await Account.objects.filter(pk=ann.pk).update(balance=F("balance") - 50)
            raise Boom()
    assert await balances() == {"ann": 90, "bob": 5}
    assert await Entry.objects.count() == 1


async def test_nested_blocks_use_savepoints(database):
    ann = await setup()
    async with transaction():
        await Account.objects.filter(pk=ann.pk).update(balance=1)
        with pytest.raises(IntegrityError):
            async with transaction():
                await Account.objects.create(owner="dup")
                await Account.objects.create(owner="ann")  # unique violation
        # The outer transaction is still usable on both databases.
        await Account.objects.create(owner="eve")
    assert await balances() == {"ann": 1, "eve": 0}

    with pytest.raises(Boom):
        async with transaction():
            async with transaction():
                await Account.objects.create(owner="inner")
            raise Boom()
    assert "inner" not in await balances(), "released savepoints roll back with the outer block"


async def test_decorator_bulk_and_child_tasks(database):
    ann = await setup()

    @transaction()
    async def transfer(amount: int) -> None:
        await Entry.objects.bulk_create([Entry(account=ann, amount=amount) for _ in range(3)])
        await asyncio.gather(
            Account.objects.filter(pk=ann.pk).update(balance=F("balance") + amount),
            Account.objects.create(owner=f"child{amount}"),
        )
        if amount < 0:
            raise Boom()

    await transfer(1)
    with pytest.raises(Boom):
        await transfer(-1)
    assert await balances() == {"ann": 101, "child1": 0}
    assert await Entry.objects.count() == 3


async def test_uncommitted_changes_are_isolated(database):
    if database == "sqlite":
        pytest.skip("an in-memory SQLite database has a single connection")
    ann = await setup()
    inside = asyncio.Event()
    release = asyncio.Event()

    async def writer() -> None:
        async with transaction():
            await Account.objects.filter(pk=ann.pk).update(balance=0)
            inside.set()
            await release.wait()

    task = asyncio.create_task(writer())
    await inside.wait()
    # Not part of the transaction: still sees the committed value.
    outside = await asyncio.create_task(
        Account.objects.get(pk=ann.pk), context=__import__("contextvars").Context()
    )
    assert outside.balance == 100
    release.set()
    await task
    assert (await Account.objects.get(pk=ann.pk)).balance == 0


async def test_cancellation_rolls_back_and_frees_the_connection(database):
    ann = await setup()
    started = asyncio.Event()

    async def slow() -> None:
        async with transaction():
            await Account.objects.filter(pk=ann.pk).update(balance=7)
            started.set()
            await asyncio.sleep(30)

    task = asyncio.create_task(slow())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await Account.objects.get(pk=ann.pk)).balance == 100


async def test_misuse_is_reported(database):
    await setup()
    leaked = []

    async with transaction():
        from ferro import _ferro

        leaked.append(_ferro.transaction_context.get())
        with pytest.raises(OperationalError, match="inside transaction"):
            await _ferro.create_tables([Account._meta.qualname])
    # A session used after its block ended fails clearly.
    token = _ferro.transaction_context.set(leaked[0])
    try:
        with pytest.raises(OperationalError, match="already ended"):
            await Account.objects.count()
    finally:
        _ferro.transaction_context.reset(token)


async def test_many_concurrent_transactions(database):
    ann = await setup()

    async def deposit(i: int) -> None:
        async with transaction():
            await Entry.objects.create(account=ann, amount=1)
            await Account.objects.filter(pk=ann.pk).update(balance=F("balance") + 1)

    await asyncio.gather(*(deposit(i) for i in range(50)))
    assert (await Account.objects.get(pk=ann.pk)).balance == 150
    assert await Entry.objects.count() == 50


async def test_first_operations_of_a_transaction(database):
    """Feature 020: on PostgreSQL BEGIN travels with the first statement; whatever comes first
    (nothing, a failing statement, a savepoint, a batch, a lock) must behave as before."""
    ann = await setup()

    async with transaction():
        pass  # nothing sent at all
    with pytest.raises(Boom):
        async with transaction():
            raise Boom()

    with pytest.raises(IntegrityError):
        async with transaction():
            await Account.objects.create(owner="ann")  # fails as the first statement
            await Account.objects.create(owner="never")

    async with transaction():
        with pytest.raises(Boom):
            async with transaction():  # a savepoint before any statement
                await Account.objects.create(owner="rolled-back")
                raise Boom()
        await Account.objects.create(owner="kept")

    with pytest.raises(Boom):
        async with transaction():
            await Entry.objects.bulk_create([Entry(account=ann, amount=1) for _ in range(2)])
            raise Boom()

    async with transaction():
        locked = await Account.objects.select_for_update().get(pk=ann.pk)
        await Account.objects.filter(pk=locked.pk).update(balance=F("balance") + 1)

    assert await balances() == {"ann": 101, "kept": 0}
    assert await Entry.objects.count() == 0
