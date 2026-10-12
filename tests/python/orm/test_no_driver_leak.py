"""FR-024 — database errors and logs never name the driver or bridge libraries."""

from decimal import Decimal
from pathlib import Path

import pytest

from ferro import ConfigurationError, db, models
from ferro.models import DatabaseError, FieldError
from tests.python.orm.conftest import use_models

INTERNALS = (
    "rusqlite",
    "libsqlite3",
    "crossbeam",
    "pyo3",
    "ferro_sqlite",
    "ferro_orm",
    "ferro_postgres",
    "tokio_postgres",
    "tokio-postgres",
    "postgres_types",
    "rustls",
)


class Ledger(models.Model):
    code = models.String(max_length=10, unique=True)
    amount = models.Decimal(max_digits=8, decimal_places=2)


def assert_clean(text: str) -> None:
    lowered = text.lower()
    for name in INTERNALS:
        assert name not in lowered, f"{name!r} leaked: {text}"


async def test_error_messages_are_clean(database, capfd, tmp_path: Path):
    messages: list[str] = []

    async def capture(operation) -> None:
        try:
            await operation()
        except (DatabaseError, FieldError, ConfigurationError) as exc:
            messages.append(f"{type(exc).__name__}: {exc}")
        else:
            pytest.fail("expected an error")

    await capture(lambda: Ledger.objects.count())  # table does not exist yet
    await use_models(Ledger)
    await Ledger.objects.create(code="a", amount=Decimal(1))
    await capture(lambda: Ledger.objects.create(code="a", amount=Decimal(1)))
    await capture(lambda: Ledger.objects.create(code="x" * 20, amount=Decimal(1)))
    await capture(lambda: db.connect("sqlite:///" + (tmp_path / "no" / "dir.db").as_posix()))
    await capture(lambda: db.connect("oracle://nope"))
    await capture(
        lambda: db.connect("postgresql://u:pw@127.0.0.1:1/x?sslmode=disable&connect_timeout=2")
    )
    db.disconnect_now()
    await capture(lambda: Ledger.objects.count())

    assert len(messages) == 7
    for message in messages:
        assert_clean(message)
    assert_clean(capfd.readouterr().err)
