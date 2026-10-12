# Contract: ORM Completion API

## `QuerySet.select_for_update(*, nowait=False, skip_locked=False) -> QuerySet`

- Lazy like every queryset method; the lock applies when the rows are fetched (`await qs`,
  `get()`, `first()`, `last()`, slicing, `values()`).
- Evaluation outside `transaction()` → `TransactionError`.
- `nowait=True` and `skip_locked=True` together → `FieldError`.
- `count()`, `exists()`, `aggregate()`, `annotate()` → `FieldError`.
- PostgreSQL SQL: `<select> [LIMIT ..] [OFFSET ..] FOR UPDATE OF "<table>" [NOWAIT | SKIP LOCKED]`.
- SQLite SQL: unchanged.

## `ManyToMany(to, *, related_name=None, through=None, through_fields=None)`

- `through`: a model class or name (resolved like foreign key targets).
- `through_fields`: `(source_fk, target_fk)` names on the link model; requires `through`.
- Validation at the moment the link model is defined (`FieldError`): link model lacks a foreign
  key to a side; several to a side without `through_fields`; `through_fields` naming a field that
  is not a foreign key to the right side; `through=` on an abstract model.

## Managers (`obj.<relation>` / reverse accessor)

- `add(*objs, through_defaults=None)`: links missing pairs only; `through_defaults` fills the link
  model's extra fields; a required extra field without a value → `FieldError` naming it;
  `through_defaults` with an automatic link model → `FieldError`.
- `set(objs, *, through_defaults=None)`: removes stale links, adds missing ones with
  `through_defaults`; existing links keep their values; one transaction.
- `remove`, `clear`, queryset methods, `prefetch_related` and lookups (`filter(members__name=...)`)
  are unchanged.

## Abstract models

```python
class Base(models.Model):
    class Meta:
        abstract = True
```

- No table, not registered, not migrated; `Base.objects.<anything>` and `Base(...)` raise
  `FieldError`.
- Subclasses: inherited fields and relations first (copied), then their own; redefining replaces,
  assigning a non-field (`None`) removes; `%(class)s` in `related_name` → snake_case model name;
  no own `Meta` → base `Meta` options (not `table`); `class Meta(Base.Meta)` extends them.
- A subclass of a concrete model → `FieldError` suggesting an abstract base.
