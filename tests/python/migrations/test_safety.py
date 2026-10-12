"""US4 - atomic migrations, SQLite rebuilds, invalid history (FR-004, FR-009, SC-003)."""

from pathlib import Path

import pytest

from ferro import models
from ferro.migrations import MigrationError, commands
from ferro.migrations.loader import load_migrations
from tests.python.migrations.conftest import model

TABLE = "safe_item"


def item(**fields):
    return model("SafeItem", {"name": models.String(max_length=50), **fields}, table=TABLE)


async def test_failing_migration_leaves_nothing(database, tmp_path):
    v1 = item(code=models.String(max_length=10, null=True))
    commands.make_migrations(tmp_path, models=[v1])
    await commands.migrate(tmp_path)
    await v1.objects.create(name="a", code="dup")
    await v1.objects.create(name="b", code="dup")
    v2 = item(
        code=models.String(max_length=10, null=True, unique=True), note=models.Text(null=True)
    )
    commands.make_migrations(tmp_path, models=[v2])
    with pytest.raises(models.DatabaseError):
        await commands.migrate(tmp_path)
    assert [applied for _, applied in await commands.status(tmp_path)] == [True, False]
    await v1.objects.create(name="c", code="dup")  # still not unique
    assert await v1.objects.count() == 3


async def test_not_null_with_existing_nulls_fails_cleanly(database, tmp_path):
    v1 = item(note=models.Text(null=True))
    commands.make_migrations(tmp_path, models=[v1])
    await commands.migrate(tmp_path)
    await v1.objects.create(name="a")
    commands.make_migrations(tmp_path, models=[item(note=models.Text())])
    with pytest.raises(models.DatabaseError):
        await commands.migrate(tmp_path)
    assert [applied for _, applied in await commands.status(tmp_path)] == [True, False]
    assert [r.note for r in await v1.objects.all()] == [None]


async def test_sqlite_rebuild_keeps_rows_indexes_and_sequence(database, tmp_path):
    if database != "sqlite":
        pytest.skip("SQLite table rebuild")
    v1 = item(note=models.Text(null=True), tag=models.String(max_length=10, index=True, null=True))
    commands.make_migrations(tmp_path, models=[v1])
    await commands.migrate(tmp_path)
    for name in ("a", "b", "c"):
        await v1.objects.create(name=name, tag=name)
    await (await v1.objects.get(name="c")).delete()  # highest id 3 deleted
    v2 = item(tag=models.String(max_length=10, index=True, null=True))
    commands.make_migrations(tmp_path, models=[v2])  # remove `note` -> rebuild
    await commands.migrate(tmp_path)
    assert sorted((r.pk, r.name) for r in await v2.objects.all()) == [(1, "a"), (2, "b")]
    fresh = await v2.objects.create(name="d")
    assert fresh.pk == 4, "AUTOINCREMENT never reuses ids, even after a rebuild"
    from ferro.migrations.loader import load_migrations as reload

    assert len(reload(tmp_path)) == 2
    assert commands.make_migrations(tmp_path, models=[v2]).operations == []


def test_conflicting_and_broken_history(tmp_path: Path):
    header = "from ferro.migrations import Migration, ops\n\n"
    (tmp_path / "0001_initial.py").write_text(header + "migration = Migration()\n")
    (tmp_path / "0002_a.py").write_text(header + 'migration = Migration(["0001_initial"])\n')
    (tmp_path / "0002_b.py").write_text(header + 'migration = Migration(["0001_initial"])\n')
    with pytest.raises(MigrationError, match=r"conflicting migrations 0002_a.py and 0002_b.py"):
        load_migrations(tmp_path)
    (tmp_path / "0002_b.py").unlink()
    (tmp_path / "0003_c.py").write_text(header + 'migration = Migration(["0001_initial"])\n')
    with pytest.raises(
        MigrationError, match=r"0003_c.py depends on \['0001_initial'\], expected \['0002_a'\]"
    ):
        load_migrations(tmp_path)
    (tmp_path / "notes.py").write_text("")
    with pytest.raises(MigrationError, match=r"unexpected file notes.py"):
        load_migrations(tmp_path)
