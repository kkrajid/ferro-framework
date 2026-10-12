"""User Story 1 — declare a model and store records (FR-001, FR-004-FR-006, FR-011, FR-013)."""

from decimal import Decimal

import pytest

from ferro import ConfigurationError, db, models
from ferro.models import FieldError, MultipleObjectsReturned, ObjectDoesNotExist
from tests.python.orm.conftest import use_models


class Gadget(models.Model):
    name = models.String(max_length=20)
    price = models.Decimal(max_digits=10, decimal_places=2)
    stock = models.Integer(default=0)
    active = models.Boolean(default=True)


async def test_implicit_id_and_defaults(database):
    await use_models(Gadget)
    gadget = Gadget(name="Laptop", price=Decimal("75000.00"))
    assert gadget.id is None
    assert gadget.pk is None
    assert gadget.stock == 0
    assert gadget.active is True
    await gadget.save()
    assert isinstance(gadget.id, int)
    assert gadget.pk == gadget.id


async def test_create_and_get_round_trip(database):
    await use_models(Gadget)
    created = await Gadget.objects.create(name="Laptop", price=Decimal("75000.00"), stock=10)
    fetched = await Gadget.objects.get(id=created.id)
    assert fetched is not created
    assert fetched == created
    assert (fetched.name, fetched.price, fetched.stock, fetched.active) == (
        "Laptop",
        Decimal("75000.00"),
        10,
        True,
    )
    assert type(fetched.name) is str
    assert type(fetched.price) is Decimal
    assert str(fetched.price) == "75000.00"
    assert type(fetched.stock) is int
    assert type(fetched.active) is bool


async def test_all_returns_instances(database):
    await use_models(Gadget)
    for i in range(3):
        await Gadget.objects.create(name=f"g{i}", price=Decimal(i))
    everything = await Gadget.objects.all()
    assert isinstance(everything, list)
    assert sorted(g.name for g in everything) == ["g0", "g1", "g2"]
    assert len(await Gadget.objects) == 3


async def test_save_updates_and_delete_removes(database):
    await use_models(Gadget)
    gadget = await Gadget.objects.create(name="Phone", price=Decimal("10.50"))
    gadget.stock = 7
    gadget.price = Decimal("9.99")
    await gadget.save()
    reloaded = await Gadget.objects.get(pk=gadget.pk)
    assert (reloaded.stock, reloaded.price) == (7, Decimal("9.99"))

    pk = gadget.pk
    await gadget.delete()
    assert gadget.pk is None
    with pytest.raises(Gadget.DoesNotExist):
        await Gadget.objects.get(pk=pk)


async def test_refresh(database):
    await use_models(Gadget)
    gadget = await Gadget.objects.create(name="Tablet", price=Decimal("1"))
    other = await Gadget.objects.get(pk=gadget.pk)
    other.stock = 42
    await other.save()
    await gadget.refresh()
    assert gadget.stock == 42
    await other.delete()
    with pytest.raises(Gadget.DoesNotExist):
        await gadget.refresh()


async def test_get_errors(database):
    await use_models(Gadget)
    with pytest.raises(Gadget.DoesNotExist) as missing:
        await Gadget.objects.get(name="nope")
    assert isinstance(missing.value, ObjectDoesNotExist)
    assert "Gadget" in str(missing.value)
    await Gadget.objects.create(name="dup", price=Decimal(1))
    await Gadget.objects.create(name="dup", price=Decimal(2))
    with pytest.raises(Gadget.MultipleObjectsReturned) as many:
        await Gadget.objects.get(name="dup")
    assert isinstance(many.value, MultipleObjectsReturned)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (
            {"name": "x" * 21, "price": Decimal(1)},
            r"Gadget\.name: value has 21 characters; max_length is 20",
        ),
        ({"name": "ok", "price": None}, r"Gadget\.price: value may not be None"),
        (
            {"name": "ok", "price": Decimal(1), "stock": "5"},
            r"Gadget\.stock: expected int, got str",
        ),
        (
            {"name": "ok", "price": Decimal(1), "active": 1},
            r"Gadget\.active: expected bool, got int",
        ),
        ({"name": 5, "price": Decimal(1)}, r"Gadget\.name: expected str, got int"),
    ],
)
async def test_invalid_values_raise_and_write_nothing(database, values, message):
    await use_models(Gadget)
    with pytest.raises(FieldError, match=message):
        await Gadget.objects.create(**values)
    assert await Gadget.objects.all() == []


def test_unknown_keyword_is_rejected():
    with pytest.raises(FieldError, match="Gadget has no field 'nme'"):
        Gadget(nme="x")


async def test_repr_and_equality(database):
    await use_models(Gadget)
    gadget = await Gadget.objects.create(name="a", price=Decimal(1))
    assert repr(gadget) == f"<Gadget pk={gadget.pk}>"
    unsaved = Gadget(name="b", price=Decimal(1))
    assert unsaved != Gadget(name="b", price=Decimal(1))


def test_table_names():
    class OrderItem(models.Model):
        quantity = models.Integer()

    class Customer(models.Model):
        name = models.String(max_length=10)

        class Meta:
            table = "customers"

    assert OrderItem._meta.table == "order_item"
    assert Customer._meta.table == "customers"
    assert OrderItem._meta.pk_name == "id"


@pytest.mark.parametrize("name", ["objects", "pk", "save", "_hidden"])
def test_reserved_field_names_are_rejected(name):
    with pytest.raises(FieldError, match="reserved"):
        type("Bad", (models.Model,), {"__module__": __name__, name: models.Integer()})


def test_table_clash_is_rejected():
    class First(models.Model):
        x = models.Integer()

        class Meta:
            table = "clash_table"

    with pytest.raises(FieldError, match=r"tables clash: .*First and .*Second"):

        class Second(models.Model):
            y = models.Integer()

            class Meta:
                table = "clash_table"


async def test_queries_need_a_database():
    db.disconnect_now()
    with pytest.raises(ConfigurationError, match="no database configured"):
        await Gadget.objects.all()


async def test_missing_table_hint(database):
    with pytest.raises(models.OperationalError, match="ferro db create-tables"):
        await Gadget.objects.all()


async def test_pk_keyword_sets_primary_key(database):
    await use_models(Gadget)
    gadget = Gadget(pk=99, name="n", price=Decimal(1))
    assert gadget.id == 99
    assert "pk" not in vars(gadget)
    await gadget.save()
    assert (await Gadget.objects.get(pk=99)).name == "n"
