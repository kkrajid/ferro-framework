"""Feature 021: explicit primary keys, isolation levels, iterator(), connection lifetime,
pattern indexes and SQLite's busy timeout."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from ferro import ConfigurationError, _ferro, db, models
from ferro.db import transaction
from ferro.models import DatabaseUnavailableError
from ferro.models.exceptions import TransactionError

from .conftest import use_models


class Ticket(models.Model):
    name = models.String(max_length=30)

    class Meta:
        table = "t21_ticket"


class Shelf(models.Model):
    label = models.String(max_length=30)

    class Meta:
        table = "t21_shelf"


class Book(models.Model):
    title = models.String(max_length=30)
    rank = models.Integer(default=0)
    shelf = models.ForeignKey(Shelf, on_delete=models.CASCADE, null=True, related_name="books")

    class Meta:
        table = "t21_book"


# -- explicit primary keys (FR-001) -------------------------------------------------------------


async def test_explicit_primary_keys_keep_the_sequence_ahead(database):
    await use_models(Ticket)
    await Ticket.objects.create(id=10, name="imported")
    after = await Ticket.objects.create(name="next")
    assert after.pk == 11, "PostgreSQL used to hand out 1 here (IntegrityError later)"
    await Ticket.objects.create(id=5, name="older")
    assert (await Ticket.objects.create(name="again")).pk == 12, "never moved backwards"
    await Ticket.objects.bulk_create([Ticket(id=40, name="bulk"), Ticket(id=41, name="bulk")])
    assert (await Ticket.objects.create(name="after bulk")).pk == 42
    checked, moved = await db.reset_sequences([Ticket])
    assert moved == []
    assert checked == (["t21_ticket"] if database == "postgres" else [])


# -- transaction(isolation=...) (FR-003) --------------------------------------------------------


async def test_isolation_levels_are_checked():
    with pytest.raises(ValueError, match="unknown isolation level"):
        transaction(isolation="chaos")
    transaction(isolation="Serializable")  # case does not matter


async def test_isolation_applies_to_the_outermost_block(database):
    await use_models(Ticket)
    async with transaction(isolation="serializable"):
        await Ticket.objects.create(name="a")
        with pytest.raises(TransactionError, match="outermost"):
            async with transaction(isolation="repeatable read"):
                pass
        async with transaction():
            await Ticket.objects.create(name="b")

    @transaction(isolation="repeatable read")
    async def add() -> None:
        await Ticket.objects.create(name="c")

    await add()
    assert await Ticket.objects.count() == 3


async def snapshot_counts(level: str) -> tuple[int, int]:
    """Counts twice inside one transaction while another connection inserts in between."""
    started, inserted = asyncio.Event(), asyncio.Event()

    async def reader() -> tuple[int, int]:
        async with transaction(isolation=level):
            first = await Ticket.objects.count()
            started.set()
            await inserted.wait()
            return first, await Ticket.objects.count()

    async def writer() -> None:
        await started.wait()
        await Ticket.objects.create(name="meanwhile")
        inserted.set()

    counts, _ = await asyncio.gather(reader(), writer())
    return counts


async def test_isolation_levels_change_what_a_transaction_sees(database):
    if database == "sqlite":
        pytest.skip("an in-memory SQLite database has a single connection")
    await use_models(Ticket)
    await Ticket.objects.create(name="first")
    assert await snapshot_counts("read committed") == (1, 2)
    assert await snapshot_counts("repeatable read") == (2, 2)
    assert await snapshot_counts("serializable") == (3, 3)


async def test_serialization_failures_ask_for_a_retry(database):
    if database == "sqlite":
        pytest.skip("an in-memory SQLite database has a single connection")
    await use_models(Ticket)
    both_read = asyncio.Barrier(2)

    async def write_skew(name: str) -> None:
        async with transaction(isolation="serializable"):
            if await Ticket.objects.count() == 0:
                await both_read.wait()
                await Ticket.objects.create(name=name)

    results = await asyncio.gather(write_skew("a"), write_skew("b"), return_exceptions=True)
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(failures) == 1, results
    assert isinstance(failures[0], DatabaseUnavailableError)
    assert await Ticket.objects.count() == 1


# -- QuerySet.iterator() (FR-004) ---------------------------------------------------------------


async def collect(queryset, chunk_size: int) -> list:
    return [item async for item in queryset.iterator(chunk_size=chunk_size)]


async def test_iterator_pages_through_every_record(database):
    await use_models(Shelf, Book)
    shelf = await Shelf.objects.create(label="s")
    await Book.objects.bulk_create(
        [Book(title=f"b{i:02d}", rank=i % 3, shelf=shelf) for i in range(25)]
    )
    everything = [b.title for b in await Book.objects.order_by("pk")]
    assert [b.title for b in await collect(Book.objects.all(), 10)] == everything
    assert [b.title for b in await collect(Book.objects.all(), 25)] == everything
    assert [b.title for b in await collect(Book.objects.all(), 100)] == everything
    # Ordered with ties: the primary key keeps chunk boundaries stable.
    ranked = await Book.objects.order_by("-rank", "pk")
    found = await collect(Book.objects.order_by("-rank"), 4)
    assert [b.title for b in found] == [b.title for b in ranked]
    flat = await collect(Book.objects.filter(rank=1).values_list("title", flat=True), 3)
    assert sorted(flat) == [t for t in everything if int(t[1:]) % 3 == 1]
    titles = await collect(Book.objects.filter(rank=1).order_by("title").values("title"), 3)
    assert titles == [{"title": b} for b in everything if int(b[1:]) % 3 == 1]
    # Slices and groups come back in one query; prefetching runs per chunk.
    assert len(await collect(Book.objects.order_by("pk")[5:9], 2)) == 4
    shelves = await collect(Shelf.objects.prefetch_related("books"), 1)
    assert len(await shelves[0].books.all()) == 25
    with pytest.raises(ValueError, match="chunk_size"):
        await collect(Book.objects.all(), 0)


async def test_iterator_survives_deletes_while_it_runs(database):
    await use_models(Ticket)
    await Ticket.objects.bulk_create([Ticket(name=f"t{i}") for i in range(30)])
    seen = []
    async for ticket in Ticket.objects.iterator(chunk_size=10):
        seen.append(ticket.name)
        if len(seen) == 10:
            # Deleting rows already seen must not make the next chunk skip any.
            await Ticket.objects.filter(name__in=seen).delete()
    assert seen == [f"t{i}" for i in range(30)]


# -- URL parameters (FR-005, FR-007) ------------------------------------------------------------


def test_url_parameters_are_validated(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    for bad in ("timeout=soon", "timeout=-1"):
        with pytest.raises(ConfigurationError, match="timeout"):
            db.connect_now(f"{url}?{bad}")
    db.connect_now(f"{url}?timeout=0.5")
    db.disconnect_now()
    for bad in ("max_lifetime=0", "max_idle=never"):
        with pytest.raises(ConfigurationError, match=bad.split("=")[0]):
            db.connect_now(f"postgresql://u@127.0.0.1:1/d?{bad}")


async def test_sqlite_busy_timeout(tmp_path):
    db.connect_now(f"sqlite:///{tmp_path.as_posix()}/busy.db?timeout=0.3")
    try:
        await use_models(Ticket)
        locked = asyncio.Event()
        release = asyncio.Event()

        async def hold_the_write_lock() -> None:
            async with transaction():
                await Ticket.objects.create(name="holder")
                locked.set()
                await release.wait()

        holder = asyncio.create_task(hold_the_write_lock())
        await locked.wait()
        began = time.monotonic()
        with pytest.raises(DatabaseUnavailableError):
            await Ticket.objects.create(name="waiter")
        waited = time.monotonic() - began
        release.set()
        await holder
        assert 0.25 <= waited < 3, waited
    finally:
        db.disconnect_now()


def backend_start(server, name: str) -> str:
    return server.query(
        f"SELECT backend_start FROM pg_stat_activity WHERE application_name = '{name}'"
    )


@pytest.mark.parametrize("parameter", ["max_lifetime", "max_idle"])
async def test_connections_are_replaced_after_their_lifetime(postgres_server, parameter):
    if postgres_server is None or postgres_server.psql is None:
        pytest.skip("PostgreSQL with psql not available")
    name = f"ferro_t21_{uuid.uuid4().hex[:8]}"
    schema = f"t_{uuid.uuid4().hex[:12]}"
    postgres_server.execute(f'CREATE SCHEMA "{schema}"')
    url = f"{postgres_server.schema_url(schema)}&application_name={name}&{parameter}=0.3"
    db.connect_now(url, pool_size=1)
    try:
        await use_models(Ticket)
        first = backend_start(postgres_server, name)
        assert first
        # Never inside a transaction: the connection outlives its limit until the block ends.
        async with transaction():
            await Ticket.objects.create(name="kept")
            await asyncio.sleep(0.5)
            assert await Ticket.objects.count() == 1
        assert backend_start(postgres_server, name) == first
        await asyncio.sleep(0.5)
        await _ferro.list_tables()
        assert backend_start(postgres_server, name) not in ("", first)
        assert await Ticket.objects.count() == 1
    finally:
        db.disconnect_now()


# -- pattern indexes (FR-006) -------------------------------------------------------------------

WORD_TABLE = f"t21_word_{uuid.uuid4().hex[:8]}"


class Word(models.Model):
    text = models.String(max_length=40, index=True)
    note = models.Text(index=True, null=True)
    count = models.Integer(index=True, default=0)

    class Meta:
        table = WORD_TABLE


async def test_text_indexes_serve_prefix_searches(database, request):
    await use_models(Word)
    await Word.objects.create(text="apple", note="red")
    assert [w.text for w in await Word.objects.filter(text__startswith="app")] == ["apple"]
    if database == "sqlite":
        return
    postgres_server = request.getfixturevalue("postgres_server")
    if postgres_server is None or postgres_server.psql is None:
        pytest.skip("psql not available")
    definitions = postgres_server.query(
        f"SELECT indexdef FROM pg_indexes WHERE tablename = '{WORD_TABLE}' ORDER BY indexname"
    ).splitlines()
    patterns = [d for d in definitions if "text_pattern_ops" in d]
    assert len(patterns) == 2, definitions  # text and note, not count
    assert all("_like" in d for d in patterns)
