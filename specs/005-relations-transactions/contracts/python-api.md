# Contract: Python API (Feature 005)

## Fields (`ferro.models`)

```python
ForeignKey(to, on_delete, *, related_name=None, null=False, column=None, default=NOT_PROVIDED)
OneToOne(to, on_delete, *, related_name=None, null=False, column=None)
ManyToMany(to, *, related_name=None)
CASCADE, SET_NULL, PROTECT, DO_NOTHING        # on_delete values
class Meta: unique_together = [("cart", "product")]
```

`to`: model class, `"self"`, `"Name"` or `"module.Name"`.

## Instances

| Expression | Result |
|---|---|
| `obj.category_id` | key value (may be `None`) |
| `obj.category` | cached instance / `None` for NULL key / `RelatedNotLoaded` |
| `obj.category = other` | sets key and cache (`other` saved, right model) or `None` |
| `obj.products` / `obj.product_set` | `RelatedManager` |
| `obj.tags` (both sides of many-to-many) | `ManyRelatedManager` |
| `customer.profile` | instance / `Profile.DoesNotExist` / `RelatedNotLoaded` |
| `await obj.fetch_related("category", "items__product")` | loads into caches, returns `obj` |
| `obj.to_dict()` | fields with relation keys as `<name>_id` |

Managers: `all()`, `filter()`, `exclude()`, `order_by()`, `count()`, `exists()`, `first()`,
`last()`, `get()`, `create(**values)`; many-to-many also `add(*objs_or_keys)`,
`remove(*objs_or_keys)`, `clear()`, `set(iterable)` (all `async`).

## QuerySet additions

```python
filter(*q, **lookups); exclude(*q, **lookups); get(*q, **lookups)
select_related(*paths); prefetch_related(*paths)
annotate(**aggregates); await aggregate(**aggregates) -> dict
values(*fields) -> awaits to list[dict]; values_list(*fields, flat=False) -> list[tuple] | list
bulk_create(objs, batch_size=None, ignore_conflicts=False)
```

## Expressions

```python
Q(**lookups) & Q(...) | ~Q(...)
F("stock") - 1; F("price") * F("quantity")
Count(path="*", *, distinct=False, default=None); Sum/Avg/Min/Max(path, *, distinct=False, default=None)
```

## Transactions (`ferro.db`)

```python
async with transaction(): ...
@transaction()
async def place_order(...): ...
```

## Errors

`RelatedNotLoaded` (subclass of `FieldError`), `ProtectedError` (subclass of `IntegrityError`),
`FieldError` for invalid paths/unsupported forms, `OperationalError` for use of a closed transaction
or schema changes inside a transaction.
