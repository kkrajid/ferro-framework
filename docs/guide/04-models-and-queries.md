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
| `sqlite:///relative/path.db`, `sqlite:////absolute/path.db` (Windows: `sqlite:///C:/data/app.db`), `sqlite:///:memory:` | file databases use WAL mode, foreign keys on, 4 connections; `?queue_size=N`, `?timeout=SECONDS` (how long a write waits for another connection's lock, default 5) |
| `postgresql://user:pass@host:port/db` (or `postgres://`) | parameters below |

PostgreSQL parameters: `sslmode` (`disable`, `prefer` (default), `require`, `verify-full`;
certificates are always verified when TLS is used), `sslrootcert=/path/ca.pem`, `pool_size`
(default 10 connections per process), `queue_size` (default 1024 waiting queries; beyond that
queries fail at once with `DatabaseUnavailableError`), `connect_timeout`, `options`
(e.g. `-csearch_path=tenant1`, `-cstatement_timeout=5000`), `application_name`, `max_lifetime`
and `max_idle` (seconds: a pooled connection older, or unused for longer, is replaced before its
next query — never inside a transaction; useful behind load balancers and failover proxies that
drop old connections, or to spread connections after a replica comes back; default: no limit).
Broken connections are reopened on the next query; passwords never appear in error messages.

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
| `annotate(n=Count("items"), label=Lower("name"))` | QuerySet with computed values ([expressions](#expressions)) |
| `distinct()` | QuerySet without duplicate rows |
| `none()` | QuerySet matching nothing |
| `select_for_update(nowait=False, skip_locked=False)` | QuerySet that locks its rows |
| `await qs` | list |
| `await qs.get(...)`, `await qs.get_or_404(...)` | one object |
| `await qs.first()`, `await qs.last()` | object or `None` (by the ordering, else primary key) |
| `await qs.count()`, `await qs.exists()` | `int`, `bool` |
| `await qs.aggregate(total=Sum("price"))` | `dict` |
| `await qs.update(stock=0)`, `await qs.delete()` | number of rows |
| `await Model.objects.create(**values)` | saved object |
| `await qs.get_or_create(defaults=None, **lookups)` | `(object, created)` |
| `await qs.update_or_create(defaults=None, create_defaults=None, **lookups)` | `(object, created)` |
| `await qs.in_bulk([1, 2], field_name="pk")` | `{key: object}` |
| `await qs.explain()` | the database's query plan, as text |
| `await Model.objects.raw(sql, params)` | objects from your own SQL ([raw SQL](#raw-sql)) |
| `await Model.objects.bulk_create(objs, batch_size=None, ignore_conflicts=False)` | the objects, keys set |
| `await Model.objects.bulk_update(objs, fields=["price"])` | number of rows |
| `async for obj in qs.iterator(chunk_size=1000)` | objects, fetched in chunks |
| `qs.sql()` | `(sql, params)` — what would run |

`iterator()` keeps memory flat for large results (exports, batch jobs): it fetches
`chunk_size` rows per query. An unordered queryset pages by primary key, so rows deleted or
added meanwhile are never repeated or skipped; an ordered one (or `values()`) pages by position
with the primary key breaking ties. `prefetch_related` runs per chunk. It holds no server-side
cursor, so it works through PgBouncer in transaction mode; sliced and grouped querysets are
fetched in one query.

### Lookups

`field__lookup=value`:

| Lookup | Meaning |
|---|---|
| `exact` (default) | `=`, or `IS NULL` for `None` |
| `iexact`, `contains`, `icontains`, `startswith`, `istartswith`, `endswith`, `iendswith` | text matching (`%`/`_` in the value are literal) |
| `regex`, `iregex` | regular expression (PostgreSQL's syntax there; Rust's `regex` syntax on SQLite) |
| `gt`, `gte`, `lt`, `lte` | comparisons |
| `range` | `price__range=(10, 20)` — `BETWEEN`, both ends included |
| `in` | `field__in=[1, 2, 3]`, or a queryset: `author__in=Author.objects.filter(...)` (one column: `values_list("f")` or the primary key) |
| `isnull` | `field__isnull=True` |

Dates and times can be compared by part — `created__year=2026`, `created__month__gte=6`,
`created__date=date(2026, 10, 20)` — with `year`, `quarter`, `month`, `day`, `week_day`
(1 = Sunday … 7 = Saturday), `hour`, `minute`, `second` and `date`. Date-times are read in UTC.

**JSON fields** are queried by key path; numbers in the path are array indexes:

```python
await Writer.objects.filter(profile__city="Kochi")
await Writer.objects.filter(profile__score__gte=5, profile__tags__0="poetry")
await Writer.objects.filter(profile__address__city__icontains="koc")
await Writer.objects.filter(profile__has_key="tags")              # has_keys=[...], has_any_keys=[...]
await Writer.objects.filter(profile__contains={"city": "Kochi"})  # containment, as PostgreSQL's @>
await Writer.objects.filter(profile__awards__isnull=True)         # key missing or JSON null
```

Keys are sent as parameters, never as SQL text.

On SQLite `iexact`/`icontains` fold ASCII letters only; PostgreSQL folds all Unicode letters
when the database has a Unicode locale (the default of managed services and of `initdb` on a
UTF-8 system; a database created with the `C` locale folds ASCII only).

### Q and F

```python
from ferro.models import F, Q

await Product.objects.filter(Q(price__lt=100) | Q(tags__name="sale"), ~Q(stock=0))
await Product.objects.filter(Q(featured=True) ^ Q(on_sale=True))      # exactly one of them
await Product.objects.filter(price__gt=F("cost") * 2)
await Product.objects.filter(pk=7).update(stock=F("stock") - 1)     # atomic in SQL
```

`F()` supports `+ - * / %`.

### Expressions

Computed per row with `annotate()`, then usable in `filter()`, `order_by()` and `values()`:

```python
from ferro.models import (
    Case, Coalesce, Concat, Exists, F, Length, Lower, OuterRef, Subquery, Value, When,
)

await Writer.objects.annotate(
    key=Lower("name"),                                            # strings name fields
    contact=Coalesce("email", Value("no email")),                 # Value() for constants
    label=Concat("name", Value(" <"), "email", Value(">")),
    size=Length("name"),
    tier=Case(
        When(novels__pages__gt=300, then=Value("epic")),
        When(Q(born__year__lt=1950) | Q(born__isnull=True), then=Value("classic")),
        default=Value("modern"),
    ),
    published=Exists(Novel.objects.filter(writer=OuterRef("pk"))),
    latest=Subquery(
        Novel.objects.filter(writer=OuterRef("pk")).order_by("-published").values_list("title")
    ),
).filter(published=True).order_by("key")
```

| Expression | Value |
|---|---|
| `Value(x)` | a constant |
| `Lower`, `Upper`, `Trim`, `Length`, `Abs` | text and number functions |
| `Concat(a, b, ...)` | joined text (NULL parts count as empty) |
| `Coalesce(a, b, ...)` | the first value that is not NULL |
| `Greatest(...)`, `Least(...)` | largest / smallest (PostgreSQL ignores NULLs; SQLite returns NULL if any is NULL) |
| `Now()` | the database's current date-time (UTC) |
| `Case(When(cond, then=...), ..., default=...)` | the first matching branch |
| `Exists(qs)` | `True` when the queryset finds a row |
| `Subquery(qs)` | the first value of a one-column queryset (`values_list("f")`, or the primary key) |
| `OuterRef("field")` | inside `Exists`/`Subquery` filters: a field of the outer row |

Limits: `OuterRef` cannot point at the same model as the subquery; expression annotations
cannot be combined with grouped `values().annotate()` aggregates, and `values()` cannot follow
a grouped `annotate()` (so a `Subquery` cannot select an aggregate); `F()` inside expressions
names the model's own fields (no relations).

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
await phones.products.add(x3, x4)                        # point saved rows here (one UPDATE)
await phones.products.remove(x3)                         # nullable keys: set to NULL
await phones.products.set([x4])                          # exactly these (nullable keys)
await phones.products.clear()

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

## Raw SQL

For queries the ORM cannot express, write SQL with `%s` for each parameter (`%%` for a
literal `%`). Values always travel as parameters; never format them into the text.

```python
from ferro import db

await db.execute("UPDATE product SET stock = stock - %s WHERE id = %s", [2, product_id])
rows = await db.fetch("SELECT category_id, SUM(stock) AS stock FROM product GROUP BY 1")
products = await Product.objects.raw(
    "SELECT p.*, ts_rank(p.search, q) AS rank FROM product p, plainto_tsquery(%s) q "
    "WHERE p.search @@ q ORDER BY rank DESC",
    [text],
)
```

- `db.execute()` returns the number of rows affected; `db.fetch()` returns dicts with plain
  values (dates, times and JSON as text); `raw()` returns model instances — select every
  column of the model; extra columns (`rank`) become attributes.
- Inside `transaction()` they join the transaction.
- Table and column names cannot be parameters: write them in the SQL yourself, never from
  user input.

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

Side effects that must wait for the commit (e-mails, jobs) go in
[`await on_commit(callback)`](18-signals-and-hooks.md#after-the-commit-on_commit).

### Isolation levels

```python
async with transaction(isolation="serializable"):
    ...
```

`isolation` is `"read committed"` (PostgreSQL's default), `"repeatable read"` (every query of
the block sees the same snapshot) or `"serializable"` (the block behaves as if transactions ran
one after another). It applies to the outermost block; a nested block that asks for one raises
`TransactionError`. Under the stricter levels PostgreSQL aborts a transaction that would break
the guarantee — that raises `DatabaseUnavailableError` (so do deadlocks), and the right reaction
is to run the whole block again:

```python
for attempt in range(3):
    try:
        async with transaction(isolation="serializable"):
            await transfer(source, target, amount)
        break
    except DatabaseUnavailableError:
        if attempt == 2:
            raise
```

SQLite transactions are already serializable (one writer at a time), so the option changes
nothing there.

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
| `DatabaseUnavailableError` | database unreachable, lost connection, lock wait (`nowait`, SQLite `timeout`), full queue, serialization failure or deadlock (retry the transaction) — answered 503 if unhandled |
| `OperationalError`, `DatabaseError` | other database errors |
| `TransactionError` | `select_for_update()` outside a transaction |

## Differences between SQLite and PostgreSQL

| | SQLite | PostgreSQL |
|---|---|---|
| Concurrent writers | one at a time (whole-database lock) | row-level locks |
| Case-insensitive lookups | ASCII only | Unicode |
| `Decimal` precision | 15 significant digits | up to 38 digits |
| NULLs in ascending order | first | last |
| Isolation | serializable | `transaction(isolation=...)`, read committed by default |
| Explicit primary keys (`create(id=10)`) | later keys continue after the highest | Ferro moves the key sequence past them |
| `startswith`/`contains` on indexed text | `LIKE` (ASCII case-insensitive) | a second `text_pattern_ops` index serves prefix searches under any collation |
| Good for | development, tests, small single-machine sites | production, several processes or machines |

### PostgreSQL notes

- **PgBouncer**: session pooling works as is. Transaction pooling needs PgBouncer 1.21+ with
  `max_prepared_statements` set, because Ferro prepares statements and caches them per
  connection; beyond that Ferro keeps no session state between transactions and no
  server-side cursors (`iterator()` included). The database broadcast backend (`LISTEN`)
  always needs session pooling or a direct connection.
- **Explicit primary keys**: inserting a row with its own `id` moves the table's key sequence
  past it (never back), so the next `create()` does not collide. For rows loaded by other tools
  (`psql`, `pg_restore` of partial data), run `ferro db sequence-reset` (or
  `await db.reset_sequences()`).
- **Pattern indexes**: `index=True` on a `String`/`Text` field also creates
  `<index>_like ... (column text_pattern_ops)`, because under a non-`C` collation the plain
  index cannot serve `LIKE 'prefix%'`. Indexes created by earlier Ferro versions do not have it; add
  it with a migration (`ops.RunSQL`) if you need it.
- **Extensions**: `ops.CreateExtension("pg_trgm")` in a migration (see
  [migrations](05-migrations.md#data-migrations-and-raw-sql)).
- **Timeouts**: `?options=-cstatement_timeout%3D5000` caps every statement at 5 s;
  `lock_timeout` works the same way.

### Faster test databases

- SQLite in memory (`sqlite:///:memory:`) is fastest; `TestClient(app, database_url=...,
  create_tables=[...])` creates the tables per client.
- For PostgreSQL tests, run the server with `fsync=off`, `synchronous_commit=off` and
  `full_page_writes=off` (never in production), and give each test its own schema
  (`?options=-csearch_path%3Dtest_42`) instead of a new database.
