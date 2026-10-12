"""Feature 005 US4: many-to-many, one-to-one and unique_together on SQLite and PostgreSQL."""

from __future__ import annotations

import pytest

from ferro import models
from ferro.models import IntegrityError, RelatedNotLoaded
from ferro.models.queryset import QuerySet

from .conftest import use_models


class Tag(models.Model):
    name = models.String(max_length=20, unique=True)

    class Meta:
        table = "m5_tag"


class Item(models.Model):
    name = models.String(max_length=20)
    tags = models.ManyToMany(Tag, related_name="items")

    class Meta:
        table = "m5_item"


class Person(models.Model):
    name = models.String(max_length=20)
    friends = models.ManyToMany("self", related_name="friend_of")

    class Meta:
        table = "m5_person"


class Customer(models.Model):
    name = models.String(max_length=20)

    class Meta:
        table = "m5_customer"


class Profile(models.Model):
    customer = models.OneToOne(Customer, on_delete=models.CASCADE, related_name="profile")
    bio = models.Text(default="")

    class Meta:
        table = "m5_profile"


class Cart(models.Model):
    name = models.String(max_length=20)

    class Meta:
        table = "m5_cart"


class CartLine(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE)
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    quantity = models.Integer(default=1)

    class Meta:
        table = "m5_cart_line"
        unique_together = (("cart", "item"),)


def names(rows) -> list[str]:
    return sorted(row.name for row in rows)


async def tagged():
    await use_models(Tag, Item)
    sale, new, old = [await Tag.objects.create(name=n) for n in ("sale", "new", "old")]
    phone, case, cable = [await Item.objects.create(name=n) for n in ("phone", "case", "cable")]
    return (sale, new, old), (phone, case, cable)


async def test_link_table_and_managers(database):
    (sale, new, old), (phone, case, cable) = await tagged()
    link = Item.tags.through
    assert link._meta.table == "m5_item_tags"
    assert link._meta.attnames == ["id", "item_id", "tag_id"]
    await phone.tags.add(sale, new, sale)  # duplicates are stored once
    await phone.tags.add(new.pk)
    await case.tags.add(sale)
    assert names(await phone.tags.all()) == ["new", "sale"]
    assert await phone.tags.count() == 2
    assert names(await sale.items.all()) == ["case", "phone"]
    assert names(await sale.items.filter(name__startswith="p")) == ["phone"]
    await phone.tags.remove(new)
    assert names(await phone.tags.all()) == ["sale"]
    await sale.items.add(cable)
    assert names(await cable.tags.all()) == ["sale"]
    await phone.tags.set([old, new])
    assert names(await phone.tags.all()) == ["new", "old"]
    await sale.items.clear()
    assert await sale.items.count() == 0
    with pytest.raises(IntegrityError):
        await link.objects.create(item=phone, tag=old)
    await phone.delete()
    assert await link.objects.filter(item_id=phone.pk).count() == 0, "links cascade"
    with pytest.raises(TypeError, match="takes Tag instances"):
        await case.tags.add(cable)
    with pytest.raises(TypeError, match="set"):
        case.tags = [sale]


async def test_lookups_from_both_sides(database):
    (sale, new, _old), (phone, case, _cable) = await tagged()
    await phone.tags.add(sale, new)
    await case.tags.add(new)
    # Two matching tags, the item is still returned once.
    assert names(await Item.objects.filter(tags__name__in=["sale", "new"])) == ["case", "phone"]
    assert names(await Item.objects.filter(tags=sale)) == ["phone"]
    assert names(await Item.objects.exclude(tags__name="new")) == ["cable"]
    assert names(await Item.objects.filter(tags__isnull=True)) == ["cable"]
    assert names(await Tag.objects.filter(items__name="case")) == ["new"]
    assert names(await Tag.objects.filter(items__isnull=True)) == ["old"]


async def test_prefetch_query_counts(database, monkeypatch):
    (sale, new, _old), (phone, case, _cable) = await tagged()
    await phone.tags.add(sale, new)
    await case.tags.add(new)
    calls: list[str] = []
    original = QuerySet._run

    async def counting(self, query):
        calls.append(self.model.__name__)
        return await original(self, query)

    monkeypatch.setattr(QuerySet, "_run", counting)
    items = await Item.objects.order_by("name").prefetch_related("tags")
    assert calls == ["Item", "Item_tags", "Tag"]
    calls.clear()
    assert [names(await item.tags.all()) for item in items] == [[], ["new"], ["new", "sale"]]
    assert calls == [], "prefetched managers do not query"
    tags = await Tag.objects.order_by("name").prefetch_related("items")
    assert [names(await tag.items.all()) for tag in tags] == [["case", "phone"], [], ["phone"]]


async def test_self_many_to_many(database):
    await use_models(Person)
    ann, bob, cid = [await Person.objects.create(name=n) for n in ("ann", "bob", "cid")]
    await ann.friends.add(bob, cid)
    assert Person.friends.through._meta.attnames == ["id", "from_person_id", "to_person_id"]
    assert names(await ann.friends.all()) == ["bob", "cid"]
    assert names(await bob.friend_of.all()) == ["ann"]
    assert names(await bob.friends.all()) == [], "not symmetrical"
    assert names(await Person.objects.filter(friends__name="cid")) == ["ann"]


async def test_one_to_one(database):
    await use_models(Customer, Profile)
    ann = await Customer.objects.create(name="ann")
    bob = await Customer.objects.create(name="bob")
    profile = await Profile.objects.create(customer=ann, bio="hi")
    assert (await Profile.objects.select_related("customer").get()).customer.name == "ann"
    fresh = await Customer.objects.get(pk=ann.pk)
    with pytest.raises(RelatedNotLoaded):
        _ = fresh.profile
    await fresh.fetch_related("profile")
    assert fresh.profile.bio == "hi"
    await bob.fetch_related("profile")
    with pytest.raises(Profile.DoesNotExist):
        _ = bob.profile
    with pytest.raises(IntegrityError):
        await Profile.objects.create(customer=ann)
    customers = await Customer.objects.order_by("name").prefetch_related("profile")
    assert [c.name for c in customers if c.__dict__["_related"]["profile"]] == ["ann"]
    assert names(await Customer.objects.filter(profile__bio="hi")) == ["ann"]
    await ann.delete()
    assert await Profile.objects.filter(pk=profile.pk).count() == 0


async def test_unique_together_and_ignore_conflicts(database):
    await use_models(Tag, Item, Cart, CartLine)
    cart = await Cart.objects.create(name="c")
    phone = await Item.objects.create(name="phone")
    case = await Item.objects.create(name="case")
    await CartLine.objects.create(cart=cart, item=phone)
    with pytest.raises(IntegrityError):
        await CartLine.objects.create(cart=cart, item=phone)
    lines = [CartLine(cart=cart, item=phone, quantity=5), CartLine(cart=cart, item=case)]
    await CartLine.objects.bulk_create(lines, ignore_conflicts=True)
    assert lines[0].pk is None and lines[1].pk is not None
    assert await CartLine.objects.count() == 2
    assert names(await Item.objects.filter(cart_line__cart=cart)) == ["case", "phone"]
    assert await cart.cart_line_set.count() == 2
