"""User Story 4 — the full field system (FR-002-FR-006, FR-016, SC-006)."""

import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from ferro import db, models
from ferro.models import FieldError, IntegrityError
from tests.python.orm.conftest import use_models


class Everything(models.Model):
    s = models.String(max_length=10, null=True)
    t = models.Text(null=True)
    i = models.Integer(null=True)
    b = models.BigInteger(null=True)
    f = models.Float(null=True)
    d = models.Decimal(max_digits=12, decimal_places=4, null=True)
    flag = models.Boolean(null=True)
    day = models.Date(null=True)
    moment = models.DateTime(null=True)
    clock = models.Time(null=True)
    ident = models.UUID(null=True)
    doc = models.JSON(null=True)


IST = timezone(timedelta(hours=5, minutes=30))

ROUND_TRIPS = [
    ("s", ""),
    ("s", "മലയാളം"),
    ("s", "x" * 10),
    ("t", ""),
    ("t", "long " * 20_000),
    ("t", "quotes ' \" and \\ backslash\nnew line"),
    ("i", -(2**31)),
    ("i", 2**31 - 1),
    ("i", 0),
    ("b", -(2**63)),
    ("b", 2**63 - 1),
    ("f", 0.0),
    ("f", -1.5e300),
    ("f", 1e-300),
    ("f", 0.1),
    ("d", Decimal("0.0000")),
    ("d", Decimal("12345678.1234")),
    ("d", Decimal("-99999999.9999")),
    ("flag", True),
    ("flag", False),
    ("day", date(1, 1, 1)),
    ("day", date(9999, 12, 31)),
    ("moment", datetime(2026, 10, 10, 9, 30, 15, 123456, tzinfo=UTC)),
    ("moment", datetime(1970, 1, 1, tzinfo=UTC)),
    ("clock", time(0, 0)),
    ("clock", time(23, 59, 59, 999999)),
    ("ident", uuid.UUID("12345678-1234-5678-1234-567812345678")),
    ("ident", uuid.UUID(int=0)),
    ("doc", {"a": [1, 2.5, None, True, "മ"], "nested": {"x": {}}}),
    ("doc", []),
    ("doc", "just text"),
    ("doc", 42),
]


@pytest.fixture
def file_database(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "fields.db"
    db.connect_now(f"sqlite:///{path.as_posix()}")
    yield path
    db.disconnect_now()


@pytest.mark.parametrize(("field", "value"), ROUND_TRIPS, ids=lambda v: str(v)[:30])
async def test_round_trip(database, field, value):
    await use_models(Everything)
    created = await Everything.objects.create(**{field: value})
    fetched = getattr(await Everything.objects.get(pk=created.pk), field)
    assert fetched == value
    assert type(fetched) is type(value)


async def test_everything_null(database):
    await use_models(Everything)
    created = await Everything.objects.create()
    fetched = await Everything.objects.get(pk=created.pk)
    assert all(getattr(fetched, name) is None for name in Everything._meta.field_names[1:])


async def test_datetime_is_stored_in_utc(file_database):
    await use_models(Everything)
    local = datetime(2026, 10, 10, 9, 30, tzinfo=IST)
    created = await Everything.objects.create(moment=local)
    fetched = (await Everything.objects.get(pk=created.pk)).moment
    assert fetched == local
    assert fetched.tzinfo == UTC
    with sqlite3.connect(file_database) as raw:
        stored = raw.execute("SELECT moment FROM everything").fetchone()[0]
    assert stored == "2026-10-10 04:00:00.000000"


async def test_decimal_is_quantized_and_numeric(file_database):
    await use_models(Everything)
    await Everything.objects.create(d=Decimal("1.5"))
    await Everything.objects.create(d=Decimal("10"))
    values = [e.d for e in await Everything.objects.order_by("d")]
    assert values == [Decimal("1.5000"), Decimal("10.0000")]
    assert [str(v) for v in values] == ["1.5000", "10.0000"]
    assert len(await Everything.objects.filter(d__gt=Decimal("9"))) == 1


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"moment": datetime(2026, 1, 1)}, "naive datetime"),
        ({"clock": time(1, 0, tzinfo=UTC)}, "timezone-aware times are not supported"),
        ({"day": datetime(2026, 1, 1, tzinfo=UTC)}, "expected datetime.date, got datetime"),
        ({"d": Decimal("1.12345")}, "5 decimal places; decimal_places is 4"),
        ({"d": Decimal("123456789.0")}, "9 digits before the decimal point"),
        ({"d": 1.5}, "floats are inexact"),
        ({"d": Decimal("NaN")}, "NaN"),
        ({"doc": {1, 2}}, "not JSON serializable"),
        ({"i": 2**31}, "outside the Integer range"),
        ({"b": 2**63}, "64-bit"),
        ({"f": float("nan")}, "NaN"),
        ({"ident": "not-a-uuid"}, "not a valid UUID"),
        ({"flag": "yes"}, "expected bool"),
        ({"i": True}, "expected int, got bool"),
    ],
)
async def test_invalid_values(database, values, message):
    await use_models(Everything)
    with pytest.raises(FieldError, match=message):
        await Everything.objects.create(**values)
    assert await Everything.objects.count() == 0


async def test_uuid_accepts_strings(database):
    await use_models(Everything)
    text = "12345678-1234-5678-1234-567812345678"
    created = await Everything.objects.create(ident=text)
    assert (await Everything.objects.get(pk=created.pk)).ident == uuid.UUID(text)
    assert len(await Everything.objects.filter(ident=uuid.UUID(text))) == 1


class WithDefaults(models.Model):
    code = models.UUID(default=uuid.uuid4)
    tags = models.JSON(default=list)
    created = models.DateTime(default=lambda: datetime.now(UTC))
    level = models.Integer(default=3)


async def test_value_and_callable_defaults(database):
    await use_models(WithDefaults)
    first, second = WithDefaults(), WithDefaults()
    assert first.code != second.code
    assert first.tags == [] and first.tags is not second.tags
    assert first.level == 3
    await first.save()
    fetched = await WithDefaults.objects.get(pk=first.pk)
    assert fetched.code == first.code
    assert fetched.created.tzinfo == UTC


class Account(models.Model):
    email = models.String(max_length=100, unique=True)
    nickname = models.String(max_length=30, index=True)
    title = models.String(max_length=30, column="display_title", null=True)


async def test_unique_index_and_column_options(file_database):
    await use_models(Account)
    original = await Account.objects.create(email="a@example.com", nickname="a")
    with pytest.raises(IntegrityError) as failure:
        await Account.objects.create(email="a@example.com", nickname="b")
    assert failure.value.model == "Account"
    assert failure.value.fields == ["email"]
    assert "Account" in str(failure.value) and "email" in str(failure.value)
    assert (await Account.objects.get(pk=original.pk)).nickname == "a"

    await Account.objects.filter(pk=original.pk).update(title="Dr")
    assert (await Account.objects.get(pk=original.pk)).title == "Dr"
    with sqlite3.connect(file_database) as raw:
        indexes = [row[1] for row in raw.execute("PRAGMA index_list(account)")]
        columns = [row[1] for row in raw.execute("PRAGMA table_info(account)")]
    assert "account_nickname_idx" in indexes
    assert columns == ["id", "email", "nickname", "display_title"]


class Token(models.Model):
    code = models.UUID(primary_key=True, default=uuid.uuid4)
    label = models.String(max_length=20)


async def test_custom_primary_key(database):
    await use_models(Token)
    assert Token._meta.pk_name == "code"
    assert "id" not in Token._meta.field_names
    token = await Token.objects.create(label="api")
    assert isinstance(token.pk, uuid.UUID)
    fetched = await Token.objects.get(pk=token.pk)
    assert fetched.label == "api"
    fetched.label = "renamed"
    await fetched.save()
    assert (await Token.objects.get(code=token.code)).label == "renamed"
    await fetched.delete()
    assert fetched.pk == token.pk, "a non-automatic key is kept after delete"
    assert await Token.objects.count() == 0


def test_field_definition_errors():
    with pytest.raises(FieldError, match="max_length"):
        type(
            "BadLength", (models.Model,), {"__module__": __name__, "x": models.String(max_length=0)}
        )
    with pytest.raises(FieldError, match="decimal_places"):
        type(
            "BadDecimal",
            (models.Model,),
            {"__module__": __name__, "x": models.Decimal(max_digits=2, decimal_places=3)},
        )
    with pytest.raises(FieldError, match="more than one primary key"):
        type(
            "TwoKeys",
            (models.Model,),
            {
                "__module__": __name__,
                "a": models.Integer(primary_key=True),
                "b": models.Integer(primary_key=True),
            },
        )
