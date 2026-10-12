"""Feature 005 US3: aggregation, annotations, values(), Q and F — every result is compared with
the same computation done in Python, on SQLite and PostgreSQL."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import pytest

from ferro import models
from ferro.models import Avg, Count, F, FieldError, Max, Min, Q, Sum

from .conftest import use_models


class Customer(models.Model):
    name = models.String(max_length=30)
    city = models.String(max_length=30, null=True)

    class Meta:
        table = "a5_customer"


class Sale(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="sales")
    amount = models.Decimal(max_digits=10, decimal_places=2)
    units = models.Integer()
    cost = models.Integer(null=True)
    note = models.String(max_length=20, null=True)

    class Meta:
        table = "a5_sale"


DATA = [
    # customer, amount, units, cost, note
    ("ann", "10.50", 1, 5, "a"),
    ("ann", "20.25", 3, None, None),
    ("bob", "99.99", 2, 150, "b"),
    ("bob", "0.01", 7, 1, "a"),
    ("bob", "5.00", 4, 9, None),
]
CITIES = {"ann": "Kochi", "bob": "Pune", "cid": None}


async def load():
    await use_models(Customer, Sale)
    customers = {}
    for name, city in CITIES.items():
        customers[name] = await Customer.objects.create(name=name, city=city)
    sales = []
    for who, amount, units, cost, note in DATA:
        sales.append(
            await Sale.objects.create(
                customer=customers[who],
                amount=Decimal(amount),
                units=units,
                cost=cost,
                note=note,
            )
        )
    return customers, sales


async def test_aggregate_matches_python(database):
    _, sales = await load()
    amounts = [s.amount for s in sales]
    result = await Sale.objects.aggregate(
        total=Sum("amount"),
        n=Count(),
        units=Sum("units"),
        avg_units=Avg("units"),
        low=Min("amount"),
        high=Max("amount"),
        notes=Count("note", distinct=True),
        costs=Count("cost"),
    )
    assert result == {
        "total": sum(amounts),
        "n": 5,
        "units": 17,
        "avg_units": pytest.approx(17 / 5),
        "low": Decimal("0.01"),
        "high": Decimal("99.99"),
        "notes": 2,
        "costs": 4,
    }
    assert isinstance(result["total"], Decimal) and isinstance(result["avg_units"], float)
    assert isinstance(result["units"], int)
    empty = await Sale.objects.filter(units__gt=100).aggregate(
        Sum("units"), Count(), total=Sum("amount", default=Decimal("0"))
    )
    assert empty == {"units__sum": None, "count": 0, "total": Decimal("0")}
    # Filters, forward relation paths and slices are respected.
    kochi = await Sale.objects.filter(customer__city="Kochi").aggregate(Sum("units"))
    assert kochi == {"units__sum": 4}
    top2 = await Sale.objects.order_by("-units")[:2].aggregate(Sum("units"))
    assert top2 == {"units__sum": 11}
    by_city = await Sale.objects.aggregate(cities=Count("customer__city", distinct=True))
    assert by_city == {"cities": 2}
    with pytest.raises(FieldError, match="many-valued"):
        await Customer.objects.aggregate(Sum("sales__units"))


async def test_annotate_each_record(database):
    _customers, sales = await load()
    rows = await Customer.objects.annotate(
        n=Count("sales"), units=Sum("sales__units"), top=Max("sales__amount")
    ).order_by("name")
    expected = defaultdict(list)
    for sale in sales:
        expected[sale.customer_id].append(sale)
    for customer in rows:
        mine = expected[customer.pk]
        assert customer.n == len(mine)
        assert customer.units == (sum(s.units for s in mine) if mine else None)
        assert customer.top == (max(s.amount for s in mine) if mine else None)
    busy = await Customer.objects.annotate(n=Count("sales")).filter(n__gte=2).order_by("-n")
    assert [c.name for c in busy] == ["bob", "ann"]
    idle = await Customer.objects.annotate(n=Count("sales")).exclude(n__gt=0)
    assert [c.name for c in idle] == ["cid"]
    with pytest.raises(FieldError, match="needs a relation path"):
        Sale.objects.annotate(total=Sum("amount"))


async def test_values_and_grouping(database):
    _, sales = await load()
    rows = await Sale.objects.order_by("pk").values("units", "customer__name")
    assert rows == [
        {"units": s.units, "customer__name": who} for s, (who, *_) in zip(sales, DATA, strict=False)
    ]
    flat = await Sale.objects.order_by("-units").values_list("units", flat=True)
    assert flat == sorted((s.units for s in sales), reverse=True)
    pairs = await Sale.objects.filter(units__lt=3).order_by("units").values_list("units", "note")
    assert pairs == [(1, "a"), (2, "b")]
    everything = await Sale.objects.filter(pk=sales[0].pk).values()
    assert everything == [sales[0].to_dict()]

    grouped = await (
        Sale.objects.values("customer__name")
        .annotate(total=Sum("amount"), n=Count())
        .order_by("customer__name")
    )
    totals = defaultdict(Decimal)
    counts = defaultdict(int)
    for who, amount, *_ in DATA:
        totals[who] += Decimal(amount)
        counts[who] += 1
    assert grouped == [
        {"customer__name": who, "total": totals[who], "n": counts[who]} for who in sorted(totals)
    ]
    big = await Sale.objects.values("customer__name").annotate(n=Count()).filter(n__gt=2)
    assert big == [{"customer__name": "bob", "n": 3}]
    assert await Sale.objects.values("note").annotate(n=Count()).count() == 3


async def test_q_objects_match_python(database):
    _, sales = await load()

    def matches(predicate) -> list[int]:
        return sorted(s.pk for s in sales if predicate(s))

    async def pks(*conditions, **lookups) -> list[int]:
        return sorted(s.pk for s in await Sale.objects.filter(*conditions, **lookups))

    assert await pks(Q(units__lt=2) | Q(units__gt=5)) == matches(
        lambda s: s.units < 2 or s.units > 5
    )
    assert await pks(~Q(note="a")) == matches(lambda s: s.note != "a")
    assert await pks(~Q(cost__gt=5)) == matches(lambda s: not (s.cost is not None and s.cost > 5))
    assert await pks(Q(note="a") | ~Q(cost__lt=10), units__gte=2) == matches(
        lambda s: (s.note == "a" or not (s.cost is not None and s.cost < 10)) and s.units >= 2
    )
    assert await pks(Q(customer__name="ann") & Q(units=3) | Q(note="b")) == matches(
        lambda s: (s.customer_id == sales[0].customer_id and s.units == 3) or s.note == "b"
    )
    assert await Sale.objects.exclude(Q(note__isnull=True) | Q(units=7)).count() == 2
    assert (await Sale.objects.get(Q(units=2))).note == "b"


async def test_f_expressions(database):
    _, sales = await load()
    over = await Sale.objects.filter(cost__gt=F("units") * 10)
    assert sorted(s.pk for s in over) == sorted(
        s.pk for s in sales if s.cost is not None and s.cost > s.units * 10
    )
    under = await Sale.objects.exclude(cost__gt=F("units"))
    assert sorted(s.pk for s in under) == sorted(
        s.pk for s in sales if not (s.cost is not None and s.cost > s.units)
    )
    changed = await Sale.objects.filter(units__gte=3).update(
        units=F("units") - 1, amount=F("amount") * 2
    )
    assert changed == 3
    after = {s.pk: s for s in await Sale.objects.all()}
    for sale in sales:
        if sale.units >= 3:
            assert after[sale.pk].units == sale.units - 1
            assert after[sale.pk].amount == sale.amount * 2
        else:
            assert after[sale.pk].units == sale.units
    with pytest.raises(FieldError, match="cannot follow relations"):
        Sale.objects.filter(units__gt=F("customer__name"))
    with pytest.raises(FieldError, match="numeric"):
        Sale.objects.filter(units__gt=F("note") + 1)
