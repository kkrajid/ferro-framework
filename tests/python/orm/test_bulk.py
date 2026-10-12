"""User Story 3 — bulk and set-based writes (FR-014, FR-015)."""

import pytest

from ferro import models
from ferro.models import FieldError, IntegrityError
from tests.python.orm.conftest import use_models


class Stock(models.Model):
    sku = models.String(max_length=20, unique=True)
    qty = models.Integer(default=0)
    active = models.Boolean(default=True)


async def seed(count: int = 5) -> list[Stock]:
    await use_models(Stock)
    return [await Stock.objects.create(sku=f"s{i}", qty=i % 3) for i in range(count)]


async def test_update_returns_affected_rows(database):
    await seed()
    changed = await Stock.objects.filter(qty=0).update(active=False, qty=9)
    assert changed == 2
    assert sorted(s.sku for s in await Stock.objects.filter(active=False)) == ["s0", "s3"]
    assert await Stock.objects.filter(qty=9).count() == 2
    assert await Stock.objects.filter(sku="none").update(qty=1) == 0


async def test_delete_returns_affected_rows(database):
    await seed()
    assert await Stock.objects.filter(qty=1).delete() == 2
    assert await Stock.objects.count() == 3
    assert await Stock.objects.all().delete() == 3
    assert await Stock.objects.count() == 0


async def test_bulk_create_assigns_primary_keys(database):
    await use_models(Stock)
    objects = [Stock(sku=f"b{i}", qty=i) for i in range(500)]
    result = await Stock.objects.bulk_create(objects)
    assert result is objects
    pks = [o.pk for o in objects]
    assert all(isinstance(pk, int) for pk in pks)
    assert pks == sorted(pks)
    assert len(set(pks)) == 500
    assert await Stock.objects.count() == 500
    assert (await Stock.objects.get(pk=pks[123])).sku == "b123"


async def test_bulk_create_is_all_or_nothing(database):
    await seed(1)
    batch = [Stock(sku="new"), Stock(sku="s0")]
    with pytest.raises(IntegrityError) as failure:
        await Stock.objects.bulk_create(batch)
    assert failure.value.model == "Stock"
    assert failure.value.fields == ["sku"]
    assert await Stock.objects.count() == 1
    assert [o.pk for o in batch] == [None, None]


async def test_bulk_create_validates_before_writing(database):
    await use_models(Stock)
    with pytest.raises(FieldError, match="max_length"):
        await Stock.objects.bulk_create([Stock(sku="ok"), Stock(sku="x" * 30)])
    assert await Stock.objects.count() == 0


async def test_bulk_create_accepts_batch_size(database):
    await use_models(Stock)
    await Stock.objects.bulk_create([Stock(sku=f"x{i}") for i in range(25)], batch_size=10)
    assert await Stock.objects.count() == 25


async def test_bulk_update_writes_only_listed_fields(database):
    items = await seed(3)
    for item in items:
        item.qty = 50
        item.active = False
    assert await Stock.objects.bulk_update(items, fields=["qty"]) == 3
    stored = await Stock.objects.order_by("pk")
    assert [s.qty for s in stored] == [50, 50, 50]
    assert [s.active for s in stored] == [True, True, True]


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        (["nope"], "cannot update field 'nope'"),
        (["id"], "cannot update field 'id'"),
        ([], "at least one field"),
    ],
)
async def test_bulk_update_field_errors(database, fields, message):
    items = await seed(1)
    with pytest.raises(FieldError, match=message):
        await Stock.objects.bulk_update(items, fields=fields)


async def test_bulk_update_needs_saved_instances(database):
    await use_models(Stock)
    with pytest.raises(FieldError, match="save it first"):
        await Stock.objects.bulk_update([Stock(sku="x")], fields=["qty"])


async def test_update_errors_happen_before_writing(database):
    await seed()
    with pytest.raises(FieldError, match="Stock has no field 'nope'"):
        await Stock.objects.update(nope=1)
    with pytest.raises(FieldError, match="expected int"):
        await Stock.objects.update(qty="x")
    with pytest.raises(FieldError, match="automatic primary key"):
        await Stock.objects.update(id=5)
    with pytest.raises(FieldError, match="slice"):
        await Stock.objects.order_by("pk")[:2].update(qty=1)
    with pytest.raises(FieldError, match="slice"):
        await Stock.objects.order_by("pk")[:2].delete()
    assert [s.qty for s in await Stock.objects.order_by("pk")] == [0, 1, 2, 0, 1]


async def test_unique_violation_on_update(database):
    await seed(2)
    with pytest.raises(IntegrityError, match=r"Stock: duplicate value for unique field 'sku'"):
        await Stock.objects.filter(sku="s1").update(sku="s0")
