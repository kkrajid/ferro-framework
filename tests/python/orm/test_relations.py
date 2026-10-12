"""Feature 005 US1: foreign keys on SQLite and PostgreSQL."""

from __future__ import annotations

from decimal import Decimal

import pytest

from ferro import models
from ferro.models import (
    F,
    FieldError,
    IntegrityError,
    ProtectedError,
    RelatedNotLoaded,
)
from ferro.models.queryset import QuerySet

from .conftest import use_models


class Category(models.Model):
    name = models.String(max_length=50)
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, related_name="children"
    )

    class Meta:
        table = "r5_category"


class Product(models.Model):
    name = models.String(max_length=50)
    price = models.Decimal(max_digits=8, decimal_places=2, default=Decimal("1.00"))
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    # Refers to a model defined below: registered once Supplier exists.
    supplier = models.ForeignKey("Supplier", on_delete=models.SET_NULL, null=True)

    class Meta:
        table = "r5_product"


class Supplier(models.Model):
    name = models.String(max_length=50)

    class Meta:
        table = "r5_supplier"


class Order(models.Model):
    number = models.String(max_length=20)

    class Meta:
        table = "r5_order"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.DO_NOTHING)
    quantity = models.Integer(default=1)
    note = models.Text(null=True)

    class Meta:
        table = "r5_order_item"


ALL = (Category, Supplier, Product, Order, OrderItem)


async def shop():
    await use_models(*ALL)
    phones = await Category.objects.create(name="Phones")
    smart = await Category.objects.create(name="Smartphones", parent=phones)
    laptops = await Category.objects.create(name="Laptops")
    acme = await Supplier.objects.create(name="Acme")
    x1 = await Product.objects.create(
        name="X1", price=Decimal("499.00"), category=smart, supplier=acme
    )
    x2 = await Product.objects.create(name="X2", price=Decimal("899.00"), category=smart)
    book = await Product.objects.create(name="Book", price=Decimal("1299.00"), category=laptops)
    return phones, smart, laptops, acme, x1, x2, book


def names(rows) -> list[str]:
    return sorted(row.name for row in rows)


async def test_keys_assignment_and_constraints(database):
    phones, smart, _, acme, x1, _, _ = await shop()
    assert x1.category_id == smart.pk and x1.category is smart
    fresh = await Product.objects.get(pk=x1.pk)
    assert fresh.category_id == smart.pk and fresh.supplier_id == acme.pk
    assert fresh.to_dict() == {
        "id": x1.pk,
        "name": "X1",
        "price": Decimal("499.00"),
        "category_id": smart.pk,
        "supplier_id": acme.pk,
    }
    by_key = await Product.objects.create(name="K", category_id=phones.pk)
    assert (await Product.objects.get(pk=by_key.pk)).category_id == phones.pk
    with pytest.raises(ValueError, match="must be saved"):
        Product(name="U", category=Category(name="unsaved"))
    with pytest.raises(TypeError, match="must be a Category instance"):
        Product(name="W", category=acme)
    with pytest.raises(IntegrityError):
        await Product.objects.create(name="Bad", category_id=987654)
    # Changing the key forgets the cached object.
    x1.category_id = phones.pk
    with pytest.raises(RelatedNotLoaded):
        _ = x1.category


async def test_unloaded_relations_raise_and_loading_works(database):
    _, _smart, _, _acme, x1, x2, _ = await shop()
    product = await Product.objects.get(pk=x1.pk)
    with pytest.raises(RelatedNotLoaded, match="select_related"):
        _ = product.category
    loaded = await Product.objects.select_related("category__parent", "supplier").get(pk=x1.pk)
    assert loaded.category.name == "Smartphones"
    assert loaded.category.parent.name == "Phones"
    assert loaded.supplier.name == "Acme"
    without = await Product.objects.select_related("supplier").get(pk=x2.pk)
    assert without.supplier is None
    await product.fetch_related("category__parent")
    assert product.category.parent.name == "Phones"
    assert (await Product.objects.get(pk=x2.pk)).supplier is None, "NULL keys need no loading"
    with pytest.raises(FieldError, match="prefetch_related"):
        Product.objects.select_related("order_item")


async def test_lookups_across_relations_and_their_complement(database):
    phones, smart, laptops, acme, *_ = await shop()
    everything = await Product.objects.all()
    cases = {
        "category": dict(category=smart),
        "category_id": dict(category_id=laptops.pk),
        "category__pk": dict(category__pk=laptops.pk),
        "category__in": dict(category__in=[smart, laptops.pk]),
        "category__name": dict(category__name="Smartphones"),
        "icontains": dict(category__name__icontains="LAP"),
        "two hops": dict(category__parent__name="Phones"),
        "two hops isnull": dict(category__parent__isnull=True),
        "supplier isnull": dict(supplier__isnull=True),
        "supplier name": dict(supplier__name="Acme"),
    }
    for label, lookups in cases.items():
        found = await Product.objects.filter(**lookups)
        excluded = await Product.objects.exclude(**lookups)
        assert sorted(p.pk for p in found + excluded) == sorted(p.pk for p in everything), label
        assert not {p.pk for p in found} & {p.pk for p in excluded}, label
    assert names(await Product.objects.filter(category__parent=phones)) == ["X1", "X2"]
    assert names(await Product.objects.exclude(supplier__name="Acme")) == ["Book", "X2"]
    assert names(await Product.objects.filter(supplier=acme)) == ["X1"]
    with pytest.raises(FieldError, match="has no field 'nope'"):
        Product.objects.filter(category__nope=1)
    with pytest.raises(FieldError, match="expected a Category instance"):
        Product.objects.filter(category=acme)


async def test_reverse_manager_and_reverse_lookups(database):
    phones, smart, laptops, _acme, x1, _x2, book = await shop()
    assert names(await smart.products.all()) == ["X1", "X2"]
    assert names(await smart.products.filter(price__gt=500)) == ["X2"]
    assert await smart.products.count() == 2
    assert await laptops.products.exists()
    assert (await smart.products.order_by("-price").first()).name == "X2"
    created = await laptops.products.create(name="Pad")
    assert created.category_id == laptops.pk
    assert names(await phones.children.all()) == ["Smartphones"]
    # Two products match, the category is returned once.
    assert names(await Category.objects.filter(products__price__gt=100)) == [
        "Laptops",
        "Smartphones",
    ]
    assert names(await Category.objects.filter(products__isnull=True)) == ["Phones"]
    assert names(await Category.objects.filter(children__name="Smartphones")) == ["Phones"]
    order = await Order.objects.create(number="A1")
    await OrderItem.objects.create(order=order, product=x1, quantity=2)
    await OrderItem.objects.create(order=order, product=book, quantity=1, note="gift")
    # Default reverse names: `order_item_set` accessor, `order_item` lookup.
    assert await x1.order_item_set.count() == 1
    assert names(await Product.objects.filter(order_item__quantity__gte=2)) == ["X1"]
    # Same filter() call: one item must match both; separate calls: any items.
    assert await Order.objects.filter(items__quantity=2, items__note="gift").count() == 0
    assert await Order.objects.filter(items__quantity=2).filter(items__note="gift").count() == 1
    assert await Order.objects.filter(items__product__category__name="Laptops").count() == 1


async def test_on_delete_rules(database):
    _phones, smart, laptops, acme, x1, x2, _book = await shop()
    with pytest.raises(ProtectedError):
        await laptops.delete()
    with pytest.raises(ProtectedError):
        await Category.objects.filter(pk=smart.pk).delete()
    assert await Category.objects.count() == 3, "nothing deleted"
    await acme.delete()  # SET_NULL
    assert (await Product.objects.get(pk=x1.pk)).supplier_id is None
    order = await Order.objects.create(number="A1")
    await OrderItem.objects.create(order=order, product=x2)
    with pytest.raises(IntegrityError):  # DO_NOTHING: the constraint refuses
        await x2.delete()
    await Order.objects.filter(pk=order.pk).delete()  # CASCADE
    assert await OrderItem.objects.count() == 0
    await x2.delete()
    assert await Product.objects.filter(pk=x2.pk).count() == 0


async def test_related_ordering_updates_and_f(database):
    _, _smart, laptops, _, x1, _x2, _book = await shop()
    ordered = await Product.objects.order_by("category__name", "-price")
    assert [p.name for p in ordered] == ["Book", "X2", "X1"]
    updated = await Product.objects.filter(category__name="Smartphones").update(
        price=F("price") + 1, category=laptops
    )
    assert updated == 2
    assert names(await laptops.products.all()) == ["Book", "X1", "X2"]
    assert (await Product.objects.get(pk=x1.pk)).price == Decimal("500.00")


async def test_pending_and_clashing_definitions(database):
    class Lonely(models.Model):
        other = models.ForeignKey("NeverDefined", on_delete=models.CASCADE)

        class Meta:
            table = "r5_lonely"

    with pytest.raises(FieldError, match="'NeverDefined' is not defined"):
        Lonely.objects.all()
    with pytest.raises(FieldError, match="is not defined"):
        Lonely()
    with pytest.raises(FieldError, match="clashes"):

        class Clash(models.Model):
            category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="name")

            class Meta:
                table = "r5_clash"


async def test_select_related_is_one_query(database, monkeypatch):
    await shop()
    calls = []
    original = QuerySet._run

    async def counting(self, query):
        calls.append(self.model.__name__)
        return await original(self, query)

    monkeypatch.setattr(QuerySet, "_run", counting)
    rows = await Product.objects.select_related("category")
    assert calls == ["Product"] and all(p.category.name for p in rows)
    calls.clear()
    rows = await Product.objects.prefetch_related("category__parent")
    assert calls == ["Product", "Category", "Category"]
    assert [p.category.parent for p in rows if p.name == "Book"] == [None]
