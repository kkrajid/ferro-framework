"""Feature 013 US3: abstract base models, on SQLite and PostgreSQL."""

from __future__ import annotations

from pathlib import Path

import pytest

from ferro import _ferro, models
from ferro.migrations import commands
from ferro.models import FieldError, IntegrityError

from .conftest import use_models


class InOwner(models.Model):
    name = models.String(max_length=20)

    class Meta:
        table = "in13_owner"


class InTimestamped(models.Model):
    created = models.Integer(default=0)
    updated = models.Integer(default=0)

    class Meta:
        abstract = True

    def touch(self, now: int) -> None:
        self.updated = now


class InOwned(InTimestamped):
    owner = models.ForeignKey(InOwner, on_delete=models.CASCADE, related_name="%(class)s_set")
    slug = models.String(max_length=20)
    tags = models.ManyToMany("InLabel", related_name="%(class)s_items")

    class Meta:
        abstract = True
        unique_together = (("owner", "slug"),)


class InLabel(models.Model):
    name = models.String(max_length=20)

    class Meta:
        table = "in13_label"


class InNote(InOwned):
    body = models.Text(default="")

    class Meta(InOwned.Meta):
        table = "in13_note"


class InPhoto(InOwned):
    slug = models.String(max_length=40, null=True)  # replaces the inherited field
    url = models.String(max_length=100)
    updated = None  # removes an inherited field

    class Meta(InOwned.Meta):
        table = "in13_photo"
        unique_together = ()


ALL = (InOwner, InLabel, InNote, InPhoto)


def test_fields_are_copied_in_order():
    assert list(InNote._fields) == ["created", "updated", "owner", "slug", "body"]
    assert list(InPhoto._fields) == ["created", "owner", "slug", "url"]
    assert InNote._fields["owner"] is not InPhoto._fields["owner"]
    assert InNote._fields["owner"].model is InNote
    assert InPhoto._fields["slug"].max_length == 40
    assert InNote._meta.table == "in13_note"
    assert set(InNote._many_to_many) == {"tags"} and set(InPhoto._many_to_many) == {"tags"}


def test_abstract_models_have_no_table():
    registered = {qualname for qualname, _ in _ferro.registered_models(None)}
    assert not any(name.endswith((".InTimestamped", ".InOwned")) for name in registered)
    with pytest.raises(FieldError, match="abstract"):
        InTimestamped.objects.all()
    with pytest.raises(FieldError, match="abstract"):
        InOwned(slug="x")


def test_concrete_parents_are_refused():
    with pytest.raises(FieldError, match="abstract = True"):

        class In13Child(InOwner):
            extra = models.Integer(default=0)


async def test_subclasses_work_independently(database):
    await use_models(*ALL)
    ann = await InOwner.objects.create(name="ann")
    red = await InLabel.objects.create(name="red")
    note = await InNote.objects.create(owner=ann, slug="first", body="hello", created=5)
    note.touch(7)
    await note.save()
    photo = await InPhoto.objects.create(owner=ann, slug=None, url="http://x/1.png")
    await note.tags.add(red)
    await photo.tags.add(red)

    fresh = await InNote.objects.get(pk=note.pk)
    assert (fresh.created, fresh.updated, fresh.body) == (5, 7, "hello")
    assert [n.slug for n in await ann.in_note_set.all()] == ["first"]
    assert [p.url for p in await ann.in_photo_set.all()] == ["http://x/1.png"]
    assert [n.slug for n in await red.in_note_items.all()] == ["first"]
    assert await InPhoto.objects.filter(tags__name="red", owner__name="ann").count() == 1
    with pytest.raises(IntegrityError):
        await InNote.objects.create(owner=ann, slug="first")  # inherited unique_together
    await InPhoto.objects.create(owner=ann, slug=None, url="http://x/2.png")  # InPhoto cleared it


async def test_two_level_hierarchy_migrates(database, tmp_path: Path):
    result = commands.make_migrations(tmp_path, models=list(ALL))
    created = {op.table: op for op in result.operations if op.op == "create_table"}
    assert {"in13_note", "in13_photo", "in13_note_tags", "in13_photo_tags"} <= set(created)
    columns = [column.name for column in created["in13_note"].columns]
    assert columns == ["id", "created", "updated", "owner_id", "slug", "body"]
    await commands.migrate(tmp_path)
    assert commands.make_migrations(tmp_path, models=list(ALL)).operations == []
    ann = await InOwner.objects.create(name="ann")
    await InNote.objects.create(owner=ann, slug="a")
    assert await InNote.objects.filter(owner__name="ann").count() == 1
