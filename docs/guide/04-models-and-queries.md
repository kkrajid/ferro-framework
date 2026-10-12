# Models and queries

Ferro has its own ORM. Models are Python classes; SQL is generated and run by the Rust core with
every value sent as a bound parameter (no string building, no injection). The same models and
queries work on **SQLite** and **PostgreSQL**. All database calls are `await`ed.

## Connecting

The server connects to `database_url` at start:

```bash
FERRO_DATABASE_URL=sqlite:///db.sqlite3
FERRO_DATABASE_URL=postgresql://user:password@host:5432/dbname?sslmode=require
```

(`.env`, the environment, `--database-url` or `App(database_url=...)`.)

| URL | Notes |
|---|---|
| `sqlite:///relative/path.db`, `sqlite:////absolute/path.db` (Windows: `sqlite:///C:/data/app.db`), `sqlite:///:memory:` | file databases use WAL mode, foreign keys on, 4 connections; `?queue_size=N` |
| `postgresql://user:pass@host:port/db` (or `postgres://`) | parameters below |

PostgreSQL parameters: `sslmode` (`disable`, `prefer` (default), `require`, `verify-full`;
certificates are always verified when TLS is used), `sslrootcert=/path/ca.pem`, `pool_size`
(default 10 connections per process), `queue_size` (default 1024 waiting queries; beyond that
queries fail at once with `DatabaseUnavailableError`), `connect_timeout`, `options`
(e.g. `-csearch_path=tenant1`), `application_name`. Broken connections are reopened on the next
query; passwords never appear in error messages.

Outside the server (scripts, notebooks, tests):

```python
import asyncio
from ferro import db

async def main():
    await db.connect("sqlite:///db.sqlite3")
    print(await Product.objects.count())
    await db.disconnect()

asyncio.run(main())
```

`ferro shell` does this for you (top-level `await`, your models loaded).

## Defining models

```python
from datetime import UTC, datetime
from decimal import Decimal
import uuid

from ferro import models


def now() -> datetime:
    return datetime.now(UTC)


class Product(models.Model):
    name = models.String(max_length=200, index=True)
    sku = models.String(max_length=40, unique=True)
    price = models.Decimal(max_digits=10, decimal_places=2)
    stock = models.Integer(default=0)
    active = models.Boolean(default=True)
    attributes = models.JSON(null=True)
    created = models.DateTime(default=now)

    class Meta:
        table = "products"                      # default: snake_case class name ("product")
        unique_together = [("name", "sku")]     # composite unique index

    def __str__(self) -> str:                   # used by the admin
        return self.name


class ApiKey(models.Model):
    key = models.UUID(primary_key=True, default=uuid.uuid4)
```

### Field types

| Field | Python value | SQLite | PostgreSQL |
|---|---|---|---|
| `String(max_length=N)` | `str` (length checked) | varchar | varchar(N) |
| `Text()` | `str` | text | text |
| `Integer()` / `BigInteger()` | `int` (32 / 64 bit) | integer / bigint | integer / bigint |
| `Float()` | `float` | real | double precision |
| `Decimal(max_digits, decimal_places)` | `decimal.Decimal` | decimal (15 significant digits exact) | numeric (exact) |
| `Boolean()` | `bool` | bool | boolean |
| `Date()` / `Time()` | `date` / `time` (naive) | date / time | date / time |
| `DateTime()` | timezone-aware `datetime`, stored and returned as UTC (naive values are refused) | datetime | timestamptz |
| `UUID()` | `uuid.UUID` | char(36) | uuid |
| `JSON()` | `dict`, `list`, `str`, numbers, `bool`, `None` | text | jsonb |

Options on every field: `null=True` (allow `None`), `default=value` or `default=callable`,
`unique=True`, `index=True`, `primary_key=True`, `column="db_name"`. Without a `primary_key`
field, a model gets `id = BigInteger` auto-increment.

`Meta` options: `table`, `unique_together`, `abstract` (see [inheritance](#abstract-base-models)).

## Creating, reading, updating, deleting

```python
p = await Product.objects.create(name="Laptop", sku="L1", price=Decimal("75000.00"))

p = Product(name="Mouse", sku="M1", price=Decimal("499"))   # not saved yet
await p.save()                                              # INSERT, sets p.id
p.stock = 5
await p.save()                                              # UPDATE

p = await Product.objects.get(pk=1)        # Product.DoesNotExist / MultipleObjectsReturned
p = await Product.objects.get_or_404(sku="L1")   # in handlers: HTTP 404 when missing
await p.refresh()                          # reload from the database
await p.delete()

p.to_dict()       # {"id": 1, "name": "...", "category_id": 3, ...} — ready to return as JSON
p.pk              # the primary key, whatever its name
```

## QuerySets

A `QuerySet` describes a query; nothing runs until you `await` it (or call an `await`able
method). QuerySets are immutable — every method returns a new one — so they are safe to reuse.

```python
cheap = Product.objects.filter(price__lt=1000, active=True)
await cheap                                           # list of Product
await cheap.order_by("-price", "name")[:10]           # LIMIT 10
await cheap.exclude(stock=0).count()
```

| Method | Returns |
|---|---|
| `all()`, `filter(*q, **lookups)`, `exclude(...)` | QuerySet |
| `order_by("field", "-field", "relation__field", "pk")` | QuerySet (replaces the order) |
| `qs[a:b]`, `limit(n)`, `offset(n)` | QuerySet with LIMIT/OFFSET (no negative indexes) |
| `select_related("fk", "fk__fk")` | QuerySet loading foreign keys in the same query |
| `prefetch_related("reverse_or_m2m", ...)` | QuerySet loading relations with one extra query per level |
| `values("f", ...)`, `values_list("f", ..., flat=False)` | dicts / tuples / single values |
| `annotate(n=Count("items"))` | QuerySet with computed values |
| `select_for_update(nowait=False, skip_locked=False)` | QuerySet that locks its rows |
| `await qs` | list |
| `await qs.get(...)`, `await qs.get_or_404(...)` | one object |
| `await qs.first()`, `await qs.last()` | object or `None` (by the ordering, else primary key) |
| `await qs.count()`, `await qs.exists()` | `int`, `bool` |
| `await qs.aggregate(total=Sum("price"))` | `dict` |
| `await qs.update(stock=0)`, `await qs.delete()` | number of rows |
| `await Model.objects.create(**values)` | saved object |
| `await Model.objects.bulk_create(objs, batch_size=None, ignore_conflicts=False)` | the objects, keys set |
| `await Model.objects.bulk_update(objs, fields=["price"])` | number of rows |
| `qs.sql()` | `(sql, params)` — what would run |

### Lookups

`field__lookup=value`:

| Lookup | Meaning |
|---|---|
| `exact` (default) | `=`, or `IS NULL` for `None` |
| `iexact`, `contains`, `icontains`, `startswith`, `endswith` | text matching (`%`/`_` in the value are literal) |
| `gt`, `gte`, `lt`, `lte` | comparisons |
| `in` | `field__in=[1, 2, 3]` |
| `isnull` | `field__isnull=True` |

On SQLite `iexact`/`icontains` fold ASCII letters only; PostgreSQL folds all Unicode letters.

### Q and F

```python
from ferro.models import F, Q

await Product.objects.filter(Q(price__lt=100) | Q(tags__name="sale"), ~Q(stock=0))
await Product.objects.filter(price__gt=F("cost") * 2)
await Product.objects.filter(pk=7).update(stock=F("stock") - 1)     # atomic in SQL
```

### Aggregation

```python
from ferro.models import Avg, Count, Max, Min, Sum

await Product.objects.aggregate(total=Sum("price"), n=Count(), avg=Avg("price"))
await Category.objects.annotate(n=Count("products")).filter(n__gt=5).order_by("-n")
await Product.objects.values("category__name").annotate(stock=Sum("stock"))   # GROUP BY
await Product.objects.values_list("name", flat=True)
```

`Avg` returns `float`; `Sum`, `Min`, `Max` keep the field's type; over no rows they return
`None` (`Count` returns 0) unless `default=` is given. `Count("x", distinct=True)` counts
distinct values.

## Relations

```python
class Category(models.Model):
    name = models.String(max_length=100)
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, related_name="children")


class Tag(models.Model):
    name = models.String(max_length=30, unique=True)


class Product(models.Model):
    name = models.String(max_length=200)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    tags = models.ManyToMany(Tag, related_name="products")


class Profile(models.Model):
    customer = models.OneToOne("Customer", on_delete=models.CASCADE, related_name="profile")
```

- `ForeignKey` stores `<name>_id` with a database foreign key and an index. `on_delete`:
  `CASCADE`, `SET_NULL` (needs `null=True`), `PROTECT` (deleting raises `ProtectedError`),
  `DO_NOTHING`. The database enforces it, so `obj.delete()` and `qs.delete()` behave the same.
- `OneToOne` is a unique foreign key; the reverse side is one object.
- `ManyToMany` creates a link table `<table>_<field>`.
- The target can be a class, `"self"`, or a model name defined later in the same module
  (`"Customer"`). For a model in another module use its full path (`"shop.models.Customer"`).
- `related_name` names the reverse side (`category.products`); default `<model>_set`;
  `"+"` for none (foreign keys only).

```python
phone = await Product.objects.create(name="X1", category=phones)
phone.category_id                                        # always available
await Product.objects.filter(category__name="Phones", tags__name="sale")
await Category.objects.filter(products__price__gt=1000)  # each category once

await phones.products.all()                              # reverse manager
await phones.products.filter(stock=0).count()
await phones.products.create(name="X2")

await phone.tags.add(sale, new)                          # instances or keys
await phone.tags.remove(sale)
await phone.tags.set([sale, new])                        # exactly these
await phone.tags.clear()
```

**No hidden queries.** Reading `product.category` only works when it was loaded; otherwise
`RelatedNotLoaded` is raised (instead of an N+1 query you did not see):

```python
products = await Product.objects.select_related("category")     # JOIN, one query
products = await Product.objects.prefetch_related("tags", "category__parent")
await product.fetch_related("category")                           # one instance
```

Conditions on many-valued relations inside one `filter()` must match the same related row;
separate `filter()` calls may match different rows (as in Django).

### Many-to-many with extra data

```python
class Band(models.Model):
    name = models.String(max_length=50)
    members = models.ManyToMany("Musician", through="Membership", related_name="bands")


class Membership(models.Model):
    band = models.ForeignKey(Band, on_delete=models.CASCADE)
    musician = models.ForeignKey("Musician", on_delete=models.CASCADE)
    role = models.String(max_length=20)


await band.members.add(ann, through_defaults={"role": "guitar"})
await Musician.objects.filter(bands__name="Loud", membership__role="guitar")
```

The link model needs one foreign key to each side (name them with
`through_fields=("source", "target")` when there are more, or for `"self"`). Add
`Meta.unique_together` on the pair if concurrent `add()` calls of the same pair must never
duplicate.

## Abstract base models

```python
class Timestamped(models.Model):
    created = models.DateTime(default=now)
    updated = models.DateTime(default=now)

    class Meta:
        abstract = True


class Note(Timestamped):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="%(class)s_set")
    body = models.Text()
```

Each concrete subclass gets its own copy of the fields (parents first) and its own table;
`%(class)s` becomes the subclass's snake_case name. Set an inherited field to `None` to drop it,
redefine it to replace it. Inheriting from a concrete (non-abstract) model is not supported.

## Transactions

```python
from ferro.db import transaction

async with transaction():
    order = await Order.objects.create(customer=customer)
    sold = await Product.objects.filter(pk=pid, stock__gte=2).update(stock=F("stock") - 2)
    if not sold:
        raise OutOfStock()                 # everything in the block is rolled back
    await OrderItem.objects.create(order=order, product_id=pid, quantity=2)


@transaction()
async def place_order(...): ...
```

- Commits at the end of the block; rolls back on any exception or cancellation (including a
  request timeout).
- Nested blocks are savepoints: an inner failure caught by the caller undoes only the inner block.
- Tasks started inside the block join the transaction; other requests never see uncommitted rows.
- SQLite: a transaction holds the database write lock from its start, so keep them short.
  PostgreSQL: a failed statement aborts the transaction; wrap risky statements in a nested
  `transaction()` to recover.

### Locking rows

```python
async with transaction():
    product = await Product.objects.select_for_update().get(pk=pid)
    if product.stock < quantity:
        raise OutOfStock()
    product.stock -= quantity
    await product.save()
```

Other transactions locking or changing those rows wait until this one ends. `nowait=True`
fails at once with `DatabaseUnavailableError`; `skip_locked=True` leaves locked rows out — the
basis of a database job queue:

```python
async with transaction():
    jobs = await Job.objects.filter(done=False).select_for_update(skip_locked=True)[:10]
    for job in jobs:
        await run(job)
        job.done = True
        await job.save()
```

Locking queries must run inside `transaction()` (`TransactionError` otherwise). Only the
model's own rows are locked. On SQLite no clause is needed: the transaction already holds the
write lock.

## Errors

| Exception (`ferro.models`) | When |
|---|---|
| `Model.DoesNotExist` (`ObjectDoesNotExist`) | `get()` found nothing |
| `Model.MultipleObjectsReturned` | `get()` found several |
| `IntegrityError` (`.model`, `.fields`) | unique, not-null, foreign-key or check violation |
| `ProtectedError` | delete blocked by `PROTECT` |
| `FieldError` | unknown field or lookup, invalid value or model definition |
| `RelatedNotLoaded` | relation read without loading it |
| `DatabaseUnavailableError` | database unreachable, lost connection, lock wait (`nowait`), full queue — answered 503 if unhandled |
| `OperationalError`, `DatabaseError` | other database errors |
| `TransactionError` | `select_for_update()` outside a transaction |

## Differences between SQLite and PostgreSQL

| | SQLite | PostgreSQL |
|---|---|---|
| Concurrent writers | one at a time (whole-database lock) | row-level locks |
| Case-insensitive lookups | ASCII only | Unicode |
| `Decimal` precision | 15 significant digits | up to 38 digits |
| NULLs in ascending order | first | last |
| Good for | development, tests, small single-machine sites | production, several processes or machines |
