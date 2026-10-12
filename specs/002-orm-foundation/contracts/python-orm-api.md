# Contract: Public ORM API (Feature 002)

```python
from ferro import models, db
```

## Models

```python
class Product(models.Model):
    name = models.String(max_length=200, index=True)
    sku = models.String(max_length=40, unique=True)
    price = models.Decimal(max_digits=10, decimal_places=2)
    stock = models.Integer(default=0)
    active = models.Boolean(default=True)
    created = models.DateTime(default=lambda: datetime.now(UTC))

    class Meta:
        table = "products"          # optional; default: snake_case class name
```

Class attributes after creation: `Product.objects` (manager), `Product.DoesNotExist`,
`Product.MultipleObjectsReturned`, `Product._meta` (native metadata; private).

Instance API:

```python
p = Product(name="Laptop", sku="LP-1", price=Decimal("75000.00"))   # defaults applied
p.pk                    # primary-key value (None until saved for auto keys)
await p.save()          # insert or update
await p.delete()        # delete row
await p.refresh()       # reload from database (DoesNotExist if gone)
p.to_dict()             # {"id": 1, "name": "Laptop", ...}
repr(p)                 # <Product pk=1>
```

`Model(**kwargs)` with an unknown name raises `FieldError`.

## Fields

`String(max_length)`, `Text()`, `Integer()`, `BigInteger()`, `Float()`,
`Decimal(max_digits, decimal_places)`, `Boolean()`, `Date()`, `DateTime()`, `Time()`, `UUID()`,
`JSON()`; every field accepts `null=False`, `default=...`, `unique=False`, `index=False`,
`primary_key=False`, `column=None`.

## QuerySet (`Model.objects` is the unfiltered queryset)

| Method | Returns | Executes |
|---|---|---|
| `all()`, `filter(**lookups)`, `exclude(**lookups)` | QuerySet | no |
| `order_by(*names)` (`"-name"` descending, `"pk"` allowed) | QuerySet | no |
| `limit(n)`, `offset(n)`, `qs[a:b]` | QuerySet | no |
| `await qs` | `list[Model]` | yes |
| `await qs.get(**lookups)` | Model or `DoesNotExist` / `MultipleObjectsReturned` | yes |
| `await qs.get_or_404(**lookups)` | Model or `HTTPException(404)` | yes |
| `await qs.first()`, `await qs.last()` | Model or None | yes |
| `await qs.count()`, `await qs.exists()` | int, bool | yes |
| `await qs.create(**values)` | saved Model | yes |
| `await qs.update(**values)` | rows affected | yes |
| `await qs.delete()` | rows deleted | yes |
| `await qs.bulk_create(objs, batch_size=None)` | the objects, with pks | yes |
| `await qs.bulk_update(objs, fields, batch_size=None)` | rows updated | yes |
| `qs.sql()` | `(sql: str, params: list)` of the SELECT | no |

Querysets are immutable; every method returns a new one. Lookup suffixes: `exact`, `iexact`,
`contains`, `icontains`, `startswith`, `endswith`, `gt`, `gte`, `lt`, `lte`, `in`, `isnull`.

## Exceptions (`ferro.models`)

`FieldError`, `DatabaseError`, `IntegrityError` (`.model`, `.fields`), `OperationalError`,
`ObjectDoesNotExist`, `MultipleObjectsReturned`.

## Database lifecycle (`ferro.db`)

```python
await db.connect("sqlite:///shop.db", pool_size=None)   # reconnects if already connected
await db.disconnect()
db.is_connected() -> bool
await db.create_tables(models=None) -> tuple[list[str], list[str]]   # (created, skipped) table names
```

`App(database_url=...)` / `FERRO_DATABASE_URL` / `--database-url` connect automatically when the
server starts and disconnect when it stops.
