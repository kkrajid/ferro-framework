"""Feature 005: foreign keys, link tables and composite unique indexes in migrations, on SQLite
and PostgreSQL."""

from __future__ import annotations

from pathlib import Path

import pytest

from ferro import models
from ferro.migrations import MigrationError, commands
from ferro.models import IntegrityError, ProtectedError
from tests.python.migrations.conftest import model


def schema(on_delete: str = models.CASCADE, extra_author: dict | None = None):
    author = model(
        "MrAuthor",
        {"name": models.String(max_length=40), **(extra_author or {})},
        table="mr_author",
    )
    tag = model("MrTag", {"name": models.String(max_length=20)}, table="mr_tag")
    book = model(
        "MrBook",
        {
            "title": models.String(max_length=40),
            "author": models.ForeignKey(author, on_delete=on_delete, related_name="books"),
            "tags": models.ManyToMany(tag, related_name="books"),
        },
        table="mr_book",
    )
    return author, tag, book


async def test_initial_migration_with_relations(database, tmp_path: Path):
    author, tag, book = schema()
    result = commands.make_migrations(tmp_path, models=[book, tag, author])
    text = result.path.read_text(encoding="utf-8")
    assert 'references="mr_author.id", on_delete="cascade"' in text
    assert (
        'ops.CreateIndex("mr_book_tags", ["mr_book_id", "mr_tag_id"], '
        '"mr_book_tags_mr_book_id_mr_tag_id_uniq", unique=True)'
    ) in text
    created = [op.table for op in result.operations if op.op == "create_table"]
    assert created.index("mr_author") < created.index("mr_book") < created.index("mr_book_tags")
    await commands.migrate(tmp_path)
    assert commands.make_migrations(tmp_path, models=[book, tag, author]).operations == []

    ann = await author.objects.create(name="ann")
    novel = await book.objects.create(title="novel", author=ann)
    red = await tag.objects.create(name="red")
    await novel.tags.add(red)
    assert [b.title for b in await book.objects.filter(tags__name="red")] == ["novel"]
    with pytest.raises(IntegrityError):
        await book.objects.create(title="orphan", author_id=999)


async def test_rebuilds_keep_children_and_rules_change(database, tmp_path: Path):
    author, tag, book = schema()
    commands.make_migrations(tmp_path, models=[author, tag, book])
    await commands.migrate(tmp_path)
    ann = await author.objects.create(name="ann")
    await book.objects.create(title="a", author=ann)
    await book.objects.create(title="b", author=ann)

    # A non-null column on the referenced table: SQLite rebuilds it; children must survive.
    author, tag, book = schema(
        on_delete=models.PROTECT, extra_author={"rank": models.Integer(default=3)}
    )
    result = commands.make_migrations(tmp_path, models=[author, tag, book])
    described = [op.describe() for op in result.operations]
    assert "add column rank to mr_author" in described
    assert "alter column author_id of mr_book" in described
    await commands.migrate(tmp_path)
    assert await book.objects.filter(author_id=ann.pk).count() == 2
    assert (await author.objects.get(pk=ann.pk)).rank == 3
    with pytest.raises(ProtectedError):
        await author.objects.filter(pk=ann.pk).delete()
    with pytest.raises(IntegrityError):
        await book.objects.create(title="orphan", author_id=999)

    await commands.rollback(tmp_path)
    author, tag, book = schema()
    await author.objects.filter(pk=ann.pk).delete()  # CASCADE again
    assert await book.objects.count() == 0


async def test_new_nullable_foreign_key(database, tmp_path: Path):
    author, tag, book = schema()
    commands.make_migrations(tmp_path, models=[author, tag, book])
    await commands.migrate(tmp_path)
    novel = await book.objects.create(title="n", author=await author.objects.create(name="a"))
    editor_field = models.ForeignKey(
        author, on_delete=models.SET_NULL, null=True, related_name="edited"
    )
    book = model(
        "MrBook",
        {
            "title": models.String(max_length=40),
            "author": models.ForeignKey(author, on_delete=models.CASCADE, related_name="books"),
            "editor": editor_field,
            "tags": models.ManyToMany(tag, related_name="books"),
        },
        table="mr_book",
    )
    result = commands.make_migrations(tmp_path, models=[author, tag, book])
    assert [op.describe() for op in result.operations] == [
        "add column editor_id to mr_book",
        "create index mr_book_editor_id_idx on mr_book (editor_id)",
    ]
    await commands.migrate(tmp_path)
    bob = await author.objects.create(name="bob")
    await book.objects.filter(pk=novel.pk).update(editor=bob)
    await bob.delete()
    assert (await book.objects.get(pk=novel.pk)).editor_id is None


async def test_circular_new_models_are_refused(database, tmp_path: Path):
    first = model("MrFirst", {"name": models.String(max_length=5)}, table="mr_first")
    second = model(
        "MrSecond",
        {"first": models.ForeignKey(first, on_delete=models.CASCADE, related_name="+")},
        table="mr_second",
    )
    first = model(
        "MrFirst",
        {"second": models.ForeignKey(second, on_delete=models.CASCADE, related_name="+")},
        table="mr_first",
    )
    with pytest.raises(MigrationError, match="circular foreign keys"):
        commands.make_migrations(tmp_path, models=[first, second])
