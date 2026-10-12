"""US2 - evolve models: every change is generated, applied and keeps existing data."""

from decimal import Decimal
from pathlib import Path

import pytest

from ferro import _ferro, models
from ferro.migrations import MigrationError, commands, ops
from tests.python.migrations.conftest import model

TABLE = "evo_item"


def item(**fields):
    base = {"name": models.String(max_length=50)}
    base.update(fields)
    return model("EvoItem", base, table=TABLE)


async def start(tmp_path: Path, **fields):
    cls = item(**fields)
    commands.make_migrations(tmp_path, models=[cls])
    await commands.migrate(tmp_path)
    for name in ("a", "b"):
        await cls.objects.create(name=name)
    return cls


async def evolve(tmp_path: Path, **fields):
    cls = item(**fields)
    result = commands.make_migrations(tmp_path, models=[cls])
    assert result.path is not None
    await commands.migrate(tmp_path)
    assert commands.make_migrations(tmp_path, models=[cls]).operations == []
    return cls, result


async def test_add_nullable_column(database, tmp_path):
    await start(tmp_path)
    cls, result = await evolve(tmp_path, note=models.Text(null=True))
    assert [op.describe() for op in result.operations] == [f"add column note to {TABLE}"]
    assert result.path.name == f"0002_add_{TABLE}_note.py"
    rows = await cls.objects.order_by("pk")
    assert [(r.name, r.note) for r in rows] == [("a", None), ("b", None)]


async def test_add_non_null_column_with_default_backfills(database, tmp_path):
    await start(tmp_path)
    cls, result = await evolve(
        tmp_path,
        stock=models.Integer(default=7),
        price=models.Decimal(max_digits=8, decimal_places=2, default=Decimal("0.00")),
    )
    text = result.path.read_text(encoding="utf-8")
    assert "default=7" in text
    assert 'default=Decimal("0.00")' in text
    assert "from decimal import Decimal" in text
    assert [(r.stock, r.price) for r in await cls.objects.order_by("pk")] == [
        (7, Decimal("0.00")),
        (7, Decimal("0.00")),
    ]


async def test_add_non_null_column_without_default_is_refused(database, tmp_path):
    await start(tmp_path)
    with pytest.raises(MigrationError, match=r"EvoItem\.stock: cannot add a non-null field"):
        commands.make_migrations(tmp_path, models=[item(stock=models.Integer())])
    with pytest.raises(MigrationError, match="constant default"):
        commands.make_migrations(
            tmp_path, models=[item(stamp=models.UUID(default=__import__("uuid").uuid4))]
        )
    assert len(list(tmp_path.glob("*.py"))) == 1


async def test_remove_column_keeps_other_data(database, tmp_path):
    await start(tmp_path, note=models.Text(null=True))
    cls, result = await evolve(tmp_path)
    assert [op.describe() for op in result.operations] == [f"remove column note from {TABLE}"]
    assert sorted(r.name for r in await cls.objects.all()) == ["a", "b"]


async def test_alter_column_keeps_data(database, tmp_path):
    await start(tmp_path, code=models.String(max_length=10, null=True))
    cls = item(code=models.String(max_length=10, null=True))
    await cls.objects.filter(name="a").update(code="X1")
    cls, result = await evolve(tmp_path, code=models.String(max_length=40, unique=True, null=True))
    assert [op.describe() for op in result.operations] == [f"alter column code of {TABLE}"]
    rows = {r.name: r.code for r in await cls.objects.all()}
    assert rows == {"a": "X1", "b": None}
    with pytest.raises(models.IntegrityError):
        await cls.objects.create(name="c", code="X1")
    await cls.objects.create(name="d", code="Y" * 40)


async def test_index_added_and_removed(database, tmp_path):
    await start(tmp_path)
    _, added = await evolve(tmp_path, name=models.String(max_length=50, index=True))
    assert [type(op) for op in added.operations] == [ops.CreateIndex]
    _, removed = await evolve(tmp_path)
    assert [type(op) for op in removed.operations] == [ops.DropIndex]


async def test_new_and_removed_models(database, tmp_path):
    first = await start(tmp_path)
    other = model("EvoTag", {"label": models.String(max_length=20)}, table="evo_tag")
    result = commands.make_migrations(tmp_path, models=[first, other])
    assert [op.describe() for op in result.operations] == ["create table evo_tag"]
    await commands.migrate(tmp_path)
    await other.objects.create(label="new")
    dropped = commands.make_migrations(tmp_path, models=[first])
    assert [op.describe() for op in dropped.operations] == ["drop table evo_tag"]
    assert any("will be dropped" in w for w in dropped.warnings)
    await commands.migrate(tmp_path)
    assert "evo_tag" not in await _ferro.list_tables()


async def test_rename_warning_and_hand_written_rename(database, tmp_path):
    await start(tmp_path)
    renamed = model("EvoItem", {"title": models.String(max_length=50)}, table=TABLE)
    check = commands.make_migrations(tmp_path, models=[renamed], check=True)
    assert any("RenameColumn" in w and "'name'" in w for w in check.warnings)
    (tmp_path / "0002_rename_name.py").write_text(
        "from ferro.migrations import Migration, ops\n\n"
        "migration = Migration(\n"
        '    dependencies=["0001_initial"],\n'
        f'    operations=[ops.RenameColumn("{TABLE}", "name", "title")],\n'
        ")\n",
        encoding="utf-8",
    )
    assert await commands.migrate(tmp_path) == ["0002_rename_name"]
    assert sorted(r.title for r in await renamed.objects.all()) == ["a", "b"]
    assert commands.make_migrations(tmp_path, models=[renamed]).operations == []


async def test_name_check_and_empty_options(database, tmp_path):
    await start(tmp_path)
    changed = item(note=models.Text(null=True))
    check = commands.make_migrations(tmp_path, models=[changed], check=True)
    assert check.path is None and len(check.operations) == 1
    assert len(list(tmp_path.glob("*.py"))) == 1
    named = commands.make_migrations(tmp_path, models=[changed], name="add_notes")
    assert named.path.name == "0002_add_notes.py"
    empty = commands.make_migrations(tmp_path, models=[changed], empty=True)
    assert empty.path.name == "0003_manual.py"
    assert await commands.migrate(tmp_path) == ["0002_add_notes", "0003_manual"]
