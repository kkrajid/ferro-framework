"""Feature 021: RunSQL, RunPython and extension operations; PostgreSQL pattern indexes in
migrations."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from ferro import _ferro, models
from ferro.migrations import MigrationError, commands, ops
from ferro.migrations.loader import load_migrations
from tests.python.migrations.conftest import model

TABLE = "dm_item"
HERE = "tests.python.migrations.test_data_migrations"


def item(**fields):
    return model("DmItem", {"name": models.String(max_length=50), **fields}, table=TABLE)


Item = item(score=models.Integer(default=0))


async def fill() -> None:
    await Item.objects.create(name="python", score=2)


async def unfill() -> None:
    await Item.objects.filter(name="python").delete()


def fail() -> None:
    raise RuntimeError("data migration failed")


def write(directory: Path, number: int, name: str, dependency: str | None, body: str) -> str:
    stem = f"{number:04d}_{name}"
    source = f"""
        from ferro.migrations import Migration, ops
        import {HERE} as here

        migration = Migration(
            dependencies={[dependency] if dependency else []!r},
            operations=[
                {textwrap.indent(textwrap.dedent(body).strip(), " " * 16).strip()}
            ],
        )
    """
    (directory / f"{stem}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return stem


async def names() -> list[tuple[str, int]]:
    return sorted((i.name, i.score) for i in await Item.objects.all())


async def test_sql_and_python_steps_apply_and_revert_together(database, tmp_path):
    commands.make_migrations(tmp_path, models=[Item])
    data = write(
        tmp_path,
        2,
        "data",
        "0001_initial",
        """
        ops.RunSQL(
            ["INSERT INTO dm_item (name, score) VALUES ('sql', 1)"],
            reverse_sql="DELETE FROM dm_item WHERE name = 'sql'",
        ),
        ops.RunPython(here.fill, here.unfill),
        """,
    )
    assert await commands.migrate(tmp_path) == ["0001_initial", data]
    assert await names() == [("python", 2), ("sql", 1)]
    assert await commands.rollback(tmp_path) == [data]
    assert await names() == []
    assert await commands.status(tmp_path) == [("0001_initial", True), (data, False)]
    await commands.migrate(tmp_path)
    assert await names() == [("python", 2), ("sql", 1)]


async def test_a_failing_step_leaves_nothing_behind(database, tmp_path):
    commands.make_migrations(tmp_path, models=[Item])
    write(
        tmp_path,
        2,
        "broken",
        "0001_initial",
        """
        ops.RunSQL("INSERT INTO dm_item (name, score) VALUES ('sql', 1)"),
        ops.RunPython(here.fill),
        ops.RunPython(here.fail),
        """,
    )
    with pytest.raises(RuntimeError, match="data migration failed"):
        await commands.migrate(tmp_path)
    assert await names() == []
    assert await commands.status(tmp_path) == [("0001_initial", True), ("0002_broken", False)]


async def test_irreversible_steps_refuse_rollback(database, tmp_path):
    commands.make_migrations(tmp_path, models=[Item])
    write(
        tmp_path,
        2,
        "once",
        "0001_initial",
        "ops.RunSQL(\"INSERT INTO dm_item (name, score) VALUES ('sql', 1)\")",
    )
    write(tmp_path, 3, "python_once", "0002_once", "ops.RunPython(here.fill)")
    await commands.migrate(tmp_path)
    with pytest.raises(MigrationError, match="RunPython operation without a backward"):
        await commands.rollback(tmp_path)
    write(
        tmp_path,
        3,
        "python_once",
        "0002_once",
        "ops.RunPython(here.fill, ops.RunPython.noop)",
    )
    assert await commands.rollback(tmp_path) == ["0003_python_once"]
    with pytest.raises(MigrationError, match="RunSQL operation without reverse_sql"):
        await commands.rollback(tmp_path)
    assert await names() == [("python", 2), ("sql", 1)], "RunPython.noop undid nothing"


def test_python_steps_stay_apart_from_schema_changes(tmp_path):
    with pytest.raises(MigrationError, match="only contain RunPython"):
        ops.Migration(operations=[ops.RunPython(fill), ops.DropTable("x")])
    with pytest.raises(MigrationError, match="RunPython takes functions"):
        ops.RunPython("not a function")  # type: ignore[arg-type]
    with pytest.raises(MigrationError, match="RunSQL"):
        ops.RunSQL([])
    write(tmp_path, 1, "mixed", None, "ops.RunPython(here.fill), ops.DropTable('x')")
    with pytest.raises(MigrationError, match=r"0001_mixed.py failed to load"):
        load_migrations(tmp_path)


async def test_extensions(database, tmp_path):
    commands.make_migrations(tmp_path, models=[Item])
    write(tmp_path, 2, "extension", "0001_initial", "ops.CreateExtension('plpgsql')")
    loaded = load_migrations(tmp_path)
    previous, current = [loaded[0].as_native()], loaded[1].as_native()
    forward = _ferro.migration_sql(previous, current, True)
    backward = _ferro.migration_sql(previous, current, False)
    if database == "sqlite":
        assert forward == [] and backward == []
    else:
        assert forward == [('CREATE EXTENSION IF NOT EXISTS "plpgsql"', [])]
        assert backward == [('DROP EXTENSION IF EXISTS "plpgsql"', [])]
    # plpgsql is always installed: applying is a no-op that must succeed (never roll it back).
    assert await commands.migrate(tmp_path) == ["0001_initial", "0002_extension"]


async def test_text_indexes_get_pattern_indexes_in_migrations(database, tmp_path):
    table = "dm_word"
    v1 = model("DmWord", {"text": models.String(max_length=20, index=True)}, table=table)
    commands.make_migrations(tmp_path, models=[v1])
    v2 = model("DmWord", {"text": models.Integer(index=True, default=0)}, table=table)
    commands.make_migrations(tmp_path, models=[v2])
    loaded = load_migrations(tmp_path)
    create = [sql for sql, _ in _ferro.migration_sql([], loaded[0].as_native(), True)]
    alter = [
        sql for sql, _ in _ferro.migration_sql([loaded[0].as_native()], loaded[1].as_native(), True)
    ]
    pattern = f'CREATE INDEX "{table}_text_idx_like" ON "{table}" ("text" text_pattern_ops)'
    if database == "sqlite":
        assert not any("pattern_ops" in sql for sql in create + alter)
    else:
        assert pattern in create
        assert alter[0] == f'DROP INDEX IF EXISTS "{table}_text_idx_like"', alter
    # Both directions run on the real database.
    await commands.migrate(tmp_path)
    await commands.rollback(tmp_path, to="zero")
    await commands.migrate(tmp_path)
