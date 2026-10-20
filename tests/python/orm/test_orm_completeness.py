"""Feature 022: lookups, transforms, JSON queries, subqueries, expressions, QuerySet helpers,
reverse foreign-key managers and raw SQL — on SQLite and PostgreSQL."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from ferro import db, models
from ferro.models import (
    Case,
    Coalesce,
    Concat,
    Exists,
    F,
    FieldError,
    Greatest,
    Length,
    Lower,
    Now,
    OuterRef,
    Q,
    Subquery,
    Upper,
    Value,
    When,
)

from .conftest import use_models

UTC = dt.UTC


class Writer(models.Model):
    name = models.String(max_length=40)
    email = models.String(max_length=80, null=True)
    born = models.Date(null=True)
    profile = models.JSON(null=True)

    class Meta:
        table = "t22_writer"


class Novel(models.Model):
    title = models.String(max_length=60)
    pages = models.Integer(default=0)
    price = models.Decimal(max_digits=8, decimal_places=2, default=Decimal("0"))
    published = models.DateTime(null=True)
    writer = models.ForeignKey(Writer, on_delete=models.SET_NULL, null=True, related_name="novels")

    class Meta:
        table = "t22_novel"


async def library():
    await use_models(Writer, Novel)
    ann = await Writer.objects.create(
        name="Ann Lee",
        email="ann@example.com",
        born=dt.date(1980, 5, 17),
        profile={"city": "Kochi", "tags": ["poetry", "drama"], "score": 7, "awards": None},
    )
    bob = await Writer.objects.create(
        name="bob ray", born=dt.date(1995, 1, 2), profile={"city": "Delhi", "score": 3}
    )
    cid = await Writer.objects.create(name="Cid Moe")
    await Novel.objects.bulk_create(
        [
            Novel(
                title="Rain Song",
                pages=320,
                price=Decimal("12.50"),
                published=dt.datetime(2024, 3, 9, 14, 30, tzinfo=UTC),  # a Saturday
                writer=ann,
            ),
            Novel(
                title="Dry Season",
                pages=180,
                price=Decimal("8.00"),
                published=dt.datetime(2025, 11, 2, 8, 5, tzinfo=UTC),
                writer=ann,
            ),
            Novel(title="Blue Road", pages=250, price=Decimal("10.00"), writer=bob),
        ]
    )
    return ann, bob, cid


async def titles(queryset) -> list[str]:
    return sorted(n.title for n in await queryset)


async def names(queryset) -> list[str]:
    return sorted(w.name for w in await queryset)


# -- lookups and transforms --------------------------------------------------------------------


async def test_new_lookups(database):
    await library()
    assert await names(Writer.objects.filter(name__istartswith="BOB")) == ["bob ray"]
    assert await names(Writer.objects.filter(name__iendswith="LEE")) == ["Ann Lee"]
    assert await titles(Novel.objects.filter(pages__range=(200, 320))) == [
        "Blue Road",
        "Rain Song",
    ]
    assert await titles(Novel.objects.filter(title__regex=r"^[RB]\w+ (Song|Road)$")) == [
        "Blue Road",
        "Rain Song",
    ]
    assert await titles(Novel.objects.filter(title__iregex=r"^dry")) == ["Dry Season"]
    assert await titles(Novel.objects.exclude(title__regex="o")) == []
    with pytest.raises(FieldError, match="two values"):
        Novel.objects.filter(pages__range=(1,))
    with pytest.raises(FieldError, match="cannot be used on Integer"):
        Novel.objects.filter(pages__regex="1")


async def test_date_transforms(database):
    await library()
    published = Novel.objects.filter
    assert await titles(published(published__year=2024)) == ["Rain Song"]
    assert await titles(published(published__year__gte=2024)) == ["Dry Season", "Rain Song"]
    assert await titles(published(published__month=11, published__day=2)) == ["Dry Season"]
    assert await titles(published(published__week_day=7)) == ["Rain Song"]  # Saturday
    assert await titles(published(published__quarter=1)) == ["Rain Song"]
    assert await titles(published(published__hour__lt=12)) == ["Dry Season"]
    assert await titles(published(published__minute=30)) == ["Rain Song"]
    assert await titles(published(published__date=dt.date(2025, 11, 2))) == ["Dry Season"]
    assert await titles(published(published__year__in=[2023, 2025])) == ["Dry Season"]
    assert await names(Writer.objects.filter(born__year__lt=1990)) == ["Ann Lee"]
    assert await names(Writer.objects.filter(novels__published__year=2025)) == ["Ann Lee"]
    with pytest.raises(FieldError):
        Writer.objects.filter(born__hour=1)  # dates have no hours


async def test_json_queries(database):
    await library()
    query = Writer.objects.filter
    assert await names(query(profile__city="Kochi")) == ["Ann Lee"]
    assert await names(query(profile__score__gt=5)) == ["Ann Lee"]
    assert await names(query(profile__score__range=(1, 5))) == ["bob ray"]
    assert await names(query(profile__tags__0="poetry")) == ["Ann Lee"]
    assert await names(query(profile__city__in=["Delhi", "Pune"])) == ["bob ray"]
    assert await names(query(profile__city__istartswith="KO")) == ["Ann Lee"]
    assert await names(query(profile__has_key="tags")) == ["Ann Lee"]
    assert await names(query(profile__has_keys=["city", "score"])) == ["Ann Lee", "bob ray"]
    assert await names(query(profile__has_any_keys=["tags", "nope"])) == ["Ann Lee"]
    assert await names(query(profile__contains={"city": "Delhi"})) == ["bob ray"]
    assert await names(query(profile__tags__contains=["drama"])) == ["Ann Lee"]
    assert await names(query(profile__tags__isnull=True)) == ["Cid Moe", "bob ray"]
    assert await names(query(profile__awards__isnull=True)) == ["Ann Lee", "Cid Moe", "bob ray"]
    assert await names(query(profile__isnull=True)) == ["Cid Moe"]
    assert await names(Writer.objects.exclude(profile__city="Kochi")) == ["Cid Moe", "bob ray"]
    key = "city'; DROP TABLE t22_writer; --"
    assert await names(query(**{f"profile__{key}": "x"})) == []  # keys are parameters
    assert await Writer.objects.count() == 3


# -- subqueries and distinct ---------------------------------------------------------------------


async def test_in_querysets_and_distinct(database):
    await library()
    a_writers = Writer.objects.filter(name__startswith="A")
    assert await titles(Novel.objects.filter(writer__in=a_writers)) == ["Dry Season", "Rain Song"]
    long_ones = Novel.objects.filter(pages__gt=200).values_list("writer", flat=True)
    assert await names(Writer.objects.filter(pk__in=long_ones)) == ["Ann Lee", "bob ray"]
    assert await names(Writer.objects.exclude(pk__in=long_ones)) == ["Cid Moe"]
    with pytest.raises(FieldError, match="one field"):
        Writer.objects.filter(pk__in=Novel.objects.values_list("title", "pages"))
    with pytest.raises(FieldError, match=r"(Text|String) values"):
        Writer.objects.filter(pk__in=Novel.objects.values_list("title", flat=True))
    authors = Novel.objects.values_list("writer__name", flat=True).order_by("writer__name")
    assert await authors == ["Ann Lee", "Ann Lee", "bob ray"]
    assert await authors.distinct() == ["Ann Lee", "bob ray"]
    assert await authors.distinct().count() == 2


# -- expressions -------------------------------------------------------------------------------


async def test_expression_annotations(database):
    await library()
    writers = Writer.objects.annotate(
        lower=Lower("name"),
        upper=Upper("name"),
        size=Length("name"),
        contact=Coalesce("email", Value("none")),
        label=Concat("name", Value(" <"), Coalesce("email", Value("?")), Value(">")),
        has_novels=Exists(Novel.objects.filter(writer=OuterRef("pk"))),
        shortest=Subquery(
            Novel.objects.filter(writer=OuterRef("pk")).order_by("pages").values_list("title")
        ),
        longest=Subquery(
            Novel.objects.filter(writer=OuterRef("pk")).order_by("-pages").values_list("pages")
        ),
    ).order_by("pk")
    rows = await writers
    first = rows[0]
    assert (first.lower, first.upper, first.size) == ("ann lee", "ANN LEE", 7)
    assert first.contact == "ann@example.com" and rows[1].contact == "none"
    assert first.label == "Ann Lee <ann@example.com>" and rows[1].label == "bob ray <?>"
    assert [r.has_novels for r in rows] == [True, True, False]
    assert [r.shortest for r in rows] == ["Dry Season", "Blue Road", None]
    assert [r.longest for r in rows] == [320, 250, None]
    assert await names(writers.filter(has_novels=False)) == ["Cid Moe"]
    longest_first = await writers.filter(size__gt=0).order_by("-size", "name")
    assert [w.size for w in longest_first] == [7, 7, 7]
    graded = Novel.objects.annotate(
        kind=Case(
            When(pages__gt=300, then=Value("long")),
            When(Q(pages__lt=200) | Q(price__lt=9), then=Value("short")),
            default=Value("medium"),
        ),
        rest=F("pages") % 100,
        at_least=Greatest("pages", Value(200)),
        now=Now(),
    ).order_by("title")
    assert [(n.title, n.kind, n.rest, n.at_least) for n in await graded] == [
        ("Blue Road", "medium", 50, 250),
        ("Dry Season", "short", 80, 200),
        ("Rain Song", "long", 20, 320),
    ]
    stamp = (await graded)[0].now
    assert isinstance(stamp, dt.datetime) and abs(stamp - dt.datetime.now(UTC)).seconds < 300
    assert await graded.filter(kind="long").values_list("title", flat=True) == ["Rain Song"]
    flags = (
        await Writer.objects.annotate(
            famous=Case(When(novels__pages__gt=300, then=True), default=False)
        )
        .order_by("pk")
        .values_list("famous", flat=True)
    )
    assert flags == [True, False, False]
    with pytest.raises(FieldError, match="OuterRef"):
        await Novel.objects.filter(writer=OuterRef("pk"))
    with pytest.raises(FieldError, match="takes 1 argument"):
        Writer.objects.annotate(x=Lower("name", "email"))


async def test_q_xor(database):
    await library()
    either = Novel.objects.filter(Q(pages__gt=200) ^ Q(price__lt=11))
    assert await titles(either) == ["Dry Season", "Rain Song"]


# -- QuerySet helpers --------------------------------------------------------------------------


async def test_get_or_create_update_or_create_in_bulk_none(database):
    ann, bob, _ = await library()
    found, created = await Writer.objects.get_or_create(name="Ann Lee")
    assert (found, created) == (ann, False)
    made, created = await Writer.objects.get_or_create(
        name="Dee", defaults={"email": lambda: "dee@example.com"}
    )
    assert created and made.email == "dee@example.com"
    again, created = await Writer.objects.get_or_create(name__iexact="DEE")
    assert again == made and not created
    _, created = await Writer.objects.update_or_create(
        name="Dee", defaults={"email": "new@example.com"}
    )
    assert not created and (await Writer.objects.get(pk=made.pk)).email == "new@example.com"
    fresh, created = await Writer.objects.update_or_create(
        name="Eve", defaults={"email": "a@x"}, create_defaults={"email": "first@x"}
    )
    assert created and fresh.email == "first@x"
    by_pk = await Writer.objects.in_bulk([ann.pk, bob.pk, 999])
    assert set(by_pk) == {ann.pk, bob.pk} and by_pk[ann.pk].name == "Ann Lee"
    assert await Writer.objects.in_bulk([]) == {}
    assert len(await Writer.objects.in_bulk()) == 5
    with pytest.raises(ValueError, match="not unique"):
        await Writer.objects.in_bulk(["x"], field_name="name")
    assert await Writer.objects.none() == []
    assert await Writer.objects.none().count() == 0
    plan = await Writer.objects.filter(name="Ann Lee").explain()
    assert isinstance(plan, str) and plan


async def test_reverse_foreign_key_manager(database):
    ann, bob, cid = await library()
    road = await Novel.objects.get(title="Blue Road")
    await cid.novels.add(road)
    assert road.writer_id == cid.pk
    assert await titles(cid.novels.all()) == ["Blue Road"]
    await cid.novels.remove(road)
    assert (await Novel.objects.get(pk=road.pk)).writer_id is None
    await ann.novels.set([road])
    assert await titles(ann.novels.all()) == ["Blue Road"]
    assert await Novel.objects.filter(writer__isnull=True).count() == 2
    await ann.novels.clear()
    assert await ann.novels.all().count() == 0
    with pytest.raises(TypeError, match="Novel instances"):
        await ann.novels.add(bob)


# -- raw SQL -----------------------------------------------------------------------------------


async def test_raw_sql(database):
    _, bob, _ = await library()
    changed = await db.execute(
        "UPDATE t22_novel SET pages = pages + %s WHERE title = %s", [5, "Blue Road"]
    )
    assert changed == 1
    rows = await db.fetch(
        "SELECT title, pages FROM t22_novel WHERE pages > %s ORDER BY title", [200]
    )
    assert rows == [{"title": "Blue Road", "pages": 255}, {"title": "Rain Song", "pages": 320}]
    like = await db.fetch("SELECT name FROM t22_writer WHERE name LIKE %s", ["%o%"])
    assert sorted(r["name"] for r in like) == ["Cid Moe", "bob ray"]  # collations sort differently
    literal = await db.fetch("SELECT name FROM t22_writer WHERE name LIKE 'A%%'")
    assert literal == [{"name": "Ann Lee"}]
    writers = await Writer.objects.raw(
        "SELECT w.*, (SELECT COUNT(*) FROM t22_novel n WHERE n.writer_id = w.id) AS books "
        "FROM t22_writer w WHERE w.name <> %s ORDER BY w.id",
        ["Cid Moe"],
    )
    assert [(w.name, w.books, w.born) for w in writers] == [
        ("Ann Lee", 2, dt.date(1980, 5, 17)),
        ("bob ray", 1, dt.date(1995, 1, 2)),
    ]
    writers[1].email = "bob@example.com"
    await writers[1].save()
    assert (await Writer.objects.get(pk=bob.pk)).email == "bob@example.com"
    with pytest.raises(FieldError, match="placeholder"):
        await db.fetch("SELECT %s, %s", [1])
    with pytest.raises(FieldError, match="every column"):
        await Writer.objects.raw("SELECT id, name FROM t22_writer")
    with pytest.raises(FieldError, match="unexpected"):
        await db.fetch("SELECT 1 WHERE 'a' LIKE '%a'")
    hostile = "x'); DROP TABLE t22_writer; --"
    assert await db.fetch("SELECT id FROM t22_writer WHERE name = %s", [hostile]) == []
    assert await Writer.objects.count() == 3
    with pytest.raises(RuntimeError):
        async with db.transaction():  # raw statements join the transaction
            await db.execute("DELETE FROM t22_novel")
            assert await Novel.objects.count() == 0
            raise RuntimeError("roll back")
    assert await Novel.objects.count() == 3
