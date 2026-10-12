"""US1 - create and apply the first migration (Feature 004)."""

from pathlib import Path

from ferro import _ferro, db, models
from ferro.migrations import commands
from tests.python.migrations.conftest import model


def product_v1():
    return model(
        "MigProduct",
        {
            "name": models.String(max_length=50, index=True),
            "sku": models.String(max_length=20, unique=True),
            "stock": models.Integer(default=0),
        },
        table="mig_product",
    )


def test_initial_migration_needs_no_database(tmp_path: Path):
    db.disconnect_now()
    product = product_v1()
    result = commands.make_migrations(tmp_path, models=[product])
    assert result.path == tmp_path / "0001_initial.py"
    text = result.path.read_text(encoding="utf-8")
    assert "from ferro.migrations import Migration, ops" in text
    assert 'ops.CreateTable(\n            "mig_product",' in text
    assert 'ops.Column("id", "BigInteger", primary_key=True, auto=True)' in text
    assert 'ops.Column("sku", "String", max_length=20, unique=True)' in text
    assert 'ops.CreateIndex("mig_product", "name", "mig_product_name_idx")' in text
    assert [op.describe() for op in result.operations] == [
        "create table mig_product",
        "create index mig_product_name_idx on mig_product (name)",
    ]


async def test_migrate_creates_the_schema_and_records_history(database, tmp_path: Path):
    product = product_v1()
    commands.make_migrations(tmp_path, models=[product])
    applied = await commands.migrate(tmp_path)
    assert applied == ["0001_initial"]
    tables = await _ferro.list_tables()
    assert "mig_product" in tables and "ferro_migrations" in tables
    created = await product.objects.create(name="Pen", sku="P-1")
    assert (await product.objects.get(pk=created.pk)).stock == 0
    assert await commands.status(tmp_path) == [("0001_initial", True)]


async def test_nothing_to_do_the_second_time(database, tmp_path: Path):
    product = product_v1()
    commands.make_migrations(tmp_path, models=[product])
    await commands.migrate(tmp_path)
    again = commands.make_migrations(tmp_path, models=[product])
    assert again.path is None
    assert again.operations == []
    assert await commands.migrate(tmp_path) == []
    assert sorted(p.name for p in tmp_path.glob("*.py")) == ["0001_initial.py"]


async def test_schema_matches_create_tables(database, tmp_path: Path):
    product = product_v1()
    result = commands.make_migrations(tmp_path, models=[product])
    from ferro.migrations.loader import load_migrations

    (migration,) = load_migrations(tmp_path)
    statements = _ferro.migration_sql([], migration.as_native(), True)
    create = statements[0][0]
    assert create.startswith('CREATE TABLE "mig_product" (')
    assert result.operations[0].table == "mig_product"
