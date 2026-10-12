"""User Story 2 — filters, ordering and pagination (FR-007-FR-012)."""

from decimal import Decimal

import pytest

from ferro import models
from ferro.models import FieldError
from tests.python.orm.conftest import use_models


class Item(models.Model):
    name = models.String(max_length=50, null=True)
    category = models.String(max_length=20)
    price = models.Decimal(max_digits=10, decimal_places=2)
    stock = models.Integer(default=0)
    active = models.Boolean(default=True)


SEED = [
    ("Laptop", "electronics", "75000.00", 5, True),
    ("laptop bag", "accessories", "1500.00", 0, True),
    ("Phone", "electronics", "30000.00", 12, False),
    ("50% off coupon", "misc", "0.00", 100, True),
    (None, "misc", "10.00", 1, True),
    ("Mouse_pad", "accessories", "250.50", 30, True),
]


async def seed() -> None:
    await use_models(Item)
    for name, category, price, stock, active in SEED:
        await Item.objects.create(
            name=name, category=category, price=Decimal(price), stock=stock, active=active
        )


def names(items: list[Item]) -> list[str]:
    return sorted(item.name or "<none>" for item in items)


@pytest.mark.parametrize(
    ("lookups", "expected"),
    [
        ({"category": "misc"}, ["50% off coupon", "<none>"]),
        ({"name__iexact": "laptop"}, ["Laptop"]),
        ({"name__contains": "aptop"}, ["Laptop", "laptop bag"]),
        ({"name__contains": "Lap"}, ["Laptop"]),
        ({"name__icontains": "LAPTOP"}, ["Laptop", "laptop bag"]),
        ({"name__startswith": "Lap"}, ["Laptop"]),
        ({"name__endswith": "pad"}, ["Mouse_pad"]),
        ({"price__gte": Decimal("30000")}, ["Laptop", "Phone"]),
        ({"price__gt": 30000}, ["Laptop"]),
        ({"price__lt": Decimal("250.50")}, ["50% off coupon", "<none>"]),
        ({"price__lte": Decimal("250.50")}, ["50% off coupon", "<none>", "Mouse_pad"]),
        ({"stock__lt": 1}, ["laptop bag"]),
        (
            {"category__in": ["misc", "accessories"]},
            ["50% off coupon", "<none>", "Mouse_pad", "laptop bag"],
        ),
        ({"id__in": []}, []),
        ({"name__isnull": True}, ["<none>"]),
        ({"name": None}, ["<none>"]),
        ({"active": False}, ["Phone"]),
        ({"category": "electronics", "active": True}, ["Laptop"]),
    ],
)
async def test_lookups(database, lookups, expected):
    await seed()
    assert names(await Item.objects.filter(**lookups)) == expected


async def test_isnull_false(database):
    await seed()
    assert len(await Item.objects.filter(name__isnull=False)) == 5


async def test_chaining_and_exclude(database):
    await seed()
    electronics = Item.objects.filter(category="electronics")
    assert names(await electronics.filter(active=True)) == ["Laptop"]
    assert len(await Item.objects.exclude(category="misc")) == 4
    # exclude keeps rows whose column is NULL (Django semantics)
    assert names(await Item.objects.exclude(name="Laptop")) == [
        "50% off coupon",
        "<none>",
        "Mouse_pad",
        "Phone",
        "laptop bag",
    ]
    assert names(await Item.objects.filter(category="misc").exclude(name__isnull=True)) == [
        "50% off coupon"
    ]


async def test_ordering(database):
    await seed()
    by_price = await Item.objects.order_by("-price")
    assert [i.name for i in by_price][:2] == ["Laptop", "Phone"]
    grouped = await Item.objects.filter(name__isnull=False).order_by("category", "-price")
    assert [i.name for i in grouped] == [
        "laptop bag",
        "Mouse_pad",
        "Laptop",
        "Phone",
        "50% off coupon",
    ]
    assert [i.pk for i in await Item.objects.order_by("-pk")] == [6, 5, 4, 3, 2, 1]


async def test_slicing_limit_offset(database):
    await seed()
    ordered = Item.objects.order_by("price")
    assert [i.price for i in await ordered[1:3]] == [Decimal("10.00"), Decimal("250.50")]
    assert len(await ordered[:2]) == 2
    assert len(await ordered[4:]) == 2
    assert [i.price for i in await ordered.offset(2).limit(2)] == [
        Decimal("250.50"),
        Decimal("1500.00"),
    ]
    assert [i.price for i in await ordered[1:5][1:2]] == [Decimal("250.50")]
    with pytest.raises(ValueError, match="Negative indexing"):
        ordered[-1:]
    with pytest.raises(TypeError, match="first"):
        ordered[0]


async def test_count_and_exists(database):
    await seed()
    assert await Item.objects.count() == 6
    assert await Item.objects.filter(category="accessories").count() == 2
    assert await Item.objects.order_by("pk")[1:4].count() == 3
    assert await Item.objects.filter(stock__gt=50).exists() is True
    assert await Item.objects.filter(stock__gt=500).exists() is False


async def test_first_and_last(database):
    await seed()
    assert (await Item.objects.first()).pk == 1
    assert (await Item.objects.last()).pk == 6
    assert (await Item.objects.order_by("price").first()).name == "50% off coupon"
    assert (await Item.objects.order_by("price").last()).name == "Laptop"
    assert await Item.objects.filter(stock__gt=500).first() is None
    assert await Item.objects.filter(stock__gt=500).last() is None


async def test_querysets_are_immutable_and_lazy(database):
    await seed()
    active = Item.objects.filter(active=True)
    rich = active.filter(stock__gt=10)
    assert len(await active) == 5
    assert len(await rich) == 2
    await Item.objects.create(name="New", category="misc", price=Decimal(1), stock=50)
    assert len(await rich) == 3, "awaiting again runs the query again"


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: Item.objects.filter(colour=1), "Item has no field 'colour'"),
        (lambda: Item.objects.filter(price__near=1), "unknown lookup 'near'"),
        (lambda: Item.objects.order_by("colour"), "cannot order by unknown field 'colour'"),
        (lambda: Item.objects.filter(stock__contains="1"), "cannot be used on Integer field"),
        (lambda: Item.objects.filter(id__in=5), "needs a list"),
        (lambda: Item.objects.filter(id__in="abc"), "needs a list"),
        (lambda: Item.objects.filter(name__isnull="yes"), "needs True or False"),
        (lambda: Item.objects.filter(stock="5"), "Item.stock: expected int, got str"),
        (lambda: Item.objects.filter(stock__gt=None), "cannot compare with None"),
    ],
)
def test_invalid_queries_fail_when_built(build, message):
    with pytest.raises(FieldError, match=message):
        build()


def test_sql_shows_text_and_parameters():
    sql, params = Item.objects.filter(price__gte=1000, active=True).order_by("-price").sql()
    assert sql == (
        'SELECT "id", "name", "category", "price", "stock", "active" FROM "item" '
        'WHERE ("price" >= ? AND "active" = ?) ORDER BY "price" DESC'
    )
    assert params == [Decimal("1000"), 1]
