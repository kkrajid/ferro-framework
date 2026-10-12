"""US3 - rollback and status (FR-005, FR-006, FR-012)."""

from pathlib import Path

import pytest

from ferro import _ferro, models
from ferro.migrations import MigrationError, commands
from tests.python.migrations.conftest import model

TABLE = "rb_item"


def item(**fields):
    return model("RbItem", {"name": models.String(max_length=50), **fields}, table=TABLE)


async def three_migrations(tmp_path: Path):
    v1 = item()
    commands.make_migrations(tmp_path, models=[v1])
    v2 = item(note=models.Text(null=True))
    commands.make_migrations(tmp_path, models=[v2])
    v3 = item(note=models.Text(null=True), stock=models.Integer(default=1, index=True))
    commands.make_migrations(tmp_path, models=[v3])
    assert await commands.migrate(tmp_path) == [
        "0001_initial",
        f"0002_add_{TABLE}_note",
        f"0003_add_{TABLE}_stock_and_more",
    ]
    await v3.objects.create(name="kept", note="n", stock=5)
    return v1, v2, v3


async def test_rollback_last_then_reapply(database, tmp_path):
    _, v2, v3 = await three_migrations(tmp_path)
    assert await commands.rollback(tmp_path) == [f"0003_add_{TABLE}_stock_and_more"]
    rows = await v2.objects.all()
    assert [(r.name, r.note) for r in rows] == [("kept", "n")]
    assert await commands.status(tmp_path) == [
        ("0001_initial", True),
        (f"0002_add_{TABLE}_note", True),
        (f"0003_add_{TABLE}_stock_and_more", False),
    ]
    await commands.migrate(tmp_path)
    assert [r.stock for r in await v3.objects.all()] == [1], "re-applied with the back-fill"


async def test_rollback_steps_to_and_zero(database, tmp_path):
    v1, _, _ = await three_migrations(tmp_path)
    assert await commands.rollback(tmp_path, steps=2) == [
        f"0003_add_{TABLE}_stock_and_more",
        f"0002_add_{TABLE}_note",
    ]
    assert [r.name for r in await v1.objects.all()] == ["kept"]
    await commands.migrate(tmp_path)
    assert await commands.rollback(tmp_path, to="0001_initial") == [
        f"0003_add_{TABLE}_stock_and_more",
        f"0002_add_{TABLE}_note",
    ]
    assert await commands.rollback(tmp_path, to="zero") == ["0001_initial"]
    assert TABLE not in await _ferro.list_tables()
    assert await commands.status(tmp_path) == [
        ("0001_initial", False),
        (f"0002_add_{TABLE}_note", False),
        (f"0003_add_{TABLE}_stock_and_more", False),
    ]
    with pytest.raises(MigrationError, match="not an applied migration"):
        await commands.rollback(tmp_path, to="0002_nope")


async def test_irreversible_rollback_changes_nothing(database, tmp_path):
    commands.make_migrations(tmp_path, models=[item(code=models.String(max_length=5))])
    commands.make_migrations(tmp_path, models=[item()])  # removes non-null `code`, no default
    applied = await commands.migrate(tmp_path)
    assert len(applied) == 2
    with pytest.raises(MigrationError, match="cannot be reversed"):
        await commands.rollback(tmp_path)
    assert await commands.status(tmp_path) == [(name, True) for name in applied]


async def test_history_without_file_is_reported(database, tmp_path):
    await three_migrations(tmp_path)
    (tmp_path / f"0003_add_{TABLE}_stock_and_more.py").unlink()
    with pytest.raises(MigrationError, match="missing files"):
        await commands.status(tmp_path)
    with pytest.raises(MigrationError, match="missing files"):
        await commands.migrate(tmp_path)
