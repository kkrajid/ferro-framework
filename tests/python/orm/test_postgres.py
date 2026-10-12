"""PostgreSQL-specific behavior (Feature 003 US2-US4). Shared ORM behavior is covered by the
backend-parametrized suite."""

from __future__ import annotations

import asyncio
import socket
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ferro import ConfigurationError, db, models
from ferro.models import OperationalError
from tests.python.orm.conftest import PostgresServer, use_models


@pytest.fixture
def pg(postgres_server: PostgresServer | None) -> Iterator[tuple[PostgresServer, str]]:
    if postgres_server is None:
        pytest.skip("PostgreSQL not available (install it or set FERRO_TEST_POSTGRES_URL)")
    schema = f"t_{uuid.uuid4().hex[:12]}"
    postgres_server.execute(f'CREATE SCHEMA "{schema}"')
    db.connect_now(postgres_server.schema_url(schema), pool_size=2)
    yield postgres_server, schema
    db.disconnect_now()


class Money(models.Model):
    amount = models.Decimal(max_digits=38, decimal_places=10)


class Kinds(models.Model):
    s = models.String(max_length=12)
    t = models.Text()
    i = models.Integer()
    b = models.BigInteger()
    f = models.Float()
    d = models.Decimal(max_digits=10, decimal_places=2)
    flag = models.Boolean()
    day = models.Date()
    moment = models.DateTime()
    clock = models.Time()
    ident = models.UUID()
    doc = models.JSON()


class Word(models.Model):
    text = models.String(max_length=40)


class Event(models.Model):
    at = models.DateTime()
    day = models.Date()
    ref = models.UUID()


# ---- US2: exact and native values -------------------------------------------------------------


async def test_38_digit_decimal_is_exact(pg):
    await use_models(Money)
    value = Decimal("1234567890123456789012345678.0123456789")
    created = await Money.objects.create(amount=value)
    stored = (await Money.objects.get(pk=created.pk)).amount
    assert stored == value
    assert str(stored) == "1234567890123456789012345678.0123456789"
    assert (
        await Money.objects.filter(
            amount__gt=Decimal("1234567890123456789012345678.0123456788")
        ).count()
        == 1
    )


async def test_native_column_types(pg):
    server, schema = pg
    await use_models(Kinds)
    rows = server.query(
        "SELECT column_name || ':' || data_type || ':' || is_identity "
        "FROM information_schema.columns "
        f"WHERE table_schema = '{schema}' AND table_name = 'kinds' ORDER BY ordinal_position"
    ).splitlines()
    assert rows == [
        "id:bigint:YES",
        "s:character varying:NO",
        "t:text:NO",
        "i:integer:NO",
        "b:bigint:NO",
        "f:double precision:NO",
        "d:numeric:NO",
        "flag:boolean:NO",
        "day:date:NO",
        "moment:timestamp with time zone:NO",
        "clock:time without time zone:NO",
        "ident:uuid:NO",
        "doc:jsonb:NO",
    ]


async def test_unicode_case_insensitive_lookups(pg):
    await use_models(Word)
    for text in ["ÉCOLE", "école", "Straße", "plain"]:
        await Word.objects.create(text=text)
    found = sorted(w.text for w in await Word.objects.filter(text__iexact="école"))
    assert found == ["ÉCOLE", "école"]
    found = sorted(w.text for w in await Word.objects.filter(text__icontains="COL"))
    assert found == ["ÉCOLE", "école"]
    assert [w.text for w in await Word.objects.filter(text__contains="col")] == ["école"]


async def test_values_compare_natively(pg):
    await use_models(Event)
    base = datetime(2026, 10, 10, 12, tzinfo=UTC)
    refs = [uuid.uuid4() for _ in range(3)]
    for offset, ref in enumerate(refs):
        await Event.objects.create(
            at=base + timedelta(days=offset), day=date(2026, 1, 1 + offset), ref=ref
        )
    assert await Event.objects.filter(at__gte=base + timedelta(hours=1)).count() == 2
    assert await Event.objects.filter(day__lt=date(2026, 1, 3)).count() == 2
    assert await Event.objects.filter(ref__in=refs[:2]).count() == 2
    latest = await Event.objects.order_by("-at").first()
    assert latest.ref == refs[2]


# ---- US3: production-ready connections --------------------------------------------------------


async def test_pool_size_caps_connections(postgres_server):
    if postgres_server is None:
        pytest.skip("PostgreSQL not available")
    schema = f"t_{uuid.uuid4().hex[:12]}"
    postgres_server.execute(f'CREATE SCHEMA "{schema}"')
    app_name = f"ferro_pool_{schema}"
    await db.connect(
        postgres_server.schema_url(schema) + f"&application_name={app_name}", pool_size=3
    )
    try:
        await use_models(Word)
        await asyncio.gather(*(Word.objects.count() for _ in range(60)))
        connections = int(
            postgres_server.query(
                f"SELECT count(*) FROM pg_stat_activity WHERE application_name = '{app_name}'"
            )
        )
        assert connections == 3
    finally:
        await db.disconnect()


async def test_reconnects_after_server_restart(postgres_server):
    if postgres_server is None or not postgres_server.can_restart:
        pytest.skip("needs the temporary local PostgreSQL cluster")
    schema = f"t_{uuid.uuid4().hex[:12]}"
    postgres_server.execute(f'CREATE SCHEMA "{schema}"')
    await db.connect(postgres_server.schema_url(schema), pool_size=1)
    try:
        await use_models(Word)
        await Word.objects.create(text="before")
        postgres_server.restart()
        assert await Word.objects.count() == 1, "first query after the restart succeeds"
        await Word.objects.create(text="after")
        assert await Word.objects.count() == 2
    finally:
        await db.disconnect()


async def test_require_tls_is_refused_by_plain_server(postgres_server):
    if postgres_server is None or not postgres_server.can_restart:
        pytest.skip("needs the temporary local PostgreSQL cluster (no TLS configured)")
    url = postgres_server.url + "?sslmode=require&connect_timeout=5"
    with pytest.raises(OperationalError, match="TLS"):
        await db.connect(url)


async def test_disable_tls_connects(postgres_server):
    if postgres_server is None:
        pytest.skip("PostgreSQL not available")
    await db.connect(postgres_server.url + "?sslmode=disable")
    assert db.is_connected()
    await db.disconnect()


@pytest.mark.parametrize(
    ("suffix", "message"),
    [
        ("?colour=blue", "unsupported PostgreSQL URL parameter 'colour'"),
        ("?sslmode=sometimes", "invalid sslmode"),
        ("?pool_size=0", "invalid pool_size"),
    ],
)
async def test_url_errors(suffix, message):
    with pytest.raises(ConfigurationError, match=message):
        await db.connect("postgresql://u:Sup3rS3cret@127.0.0.1/app" + suffix)


async def test_connection_errors_hide_the_password():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"postgresql://shop:Sup3rS3cret@127.0.0.1:{port}/shop?sslmode=disable&connect_timeout=3"
    with pytest.raises(OperationalError) as failure:
        await db.connect(url)
    message = str(failure.value)
    assert "Sup3rS3cret" not in message
    assert f"127.0.0.1:{port}/shop as shop" in message


async def test_unknown_database(postgres_server):
    if postgres_server is None:
        pytest.skip("PostgreSQL not available")
    base = postgres_server.url.rsplit("/", 1)[0]
    with pytest.raises(OperationalError, match='database "missing_db_x" does not exist'):
        await db.connect(f"{base}/missing_db_x?sslmode=disable")


# ---- US4: same exceptions as SQLite -----------------------------------------------------------


class Coded(models.Model):
    code = models.String(max_length=10, unique=True)


async def test_integrity_and_missing_table_parity(pg):
    with pytest.raises(OperationalError, match="ferro db create-tables"):
        await Coded.objects.count()
    await use_models(Coded)
    await Coded.objects.create(code="a")
    with pytest.raises(models.IntegrityError) as failure:
        await Coded.objects.create(code="a")
    assert failure.value.model == "Coded"
    assert failure.value.fields == ["code"]
