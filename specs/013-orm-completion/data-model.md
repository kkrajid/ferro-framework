# Data Model: ORM Completion

## Query (Rust, `ferro_orm::Query`)

| Field | Type | Notes |
|---|---|---|
| `lock` | `Option<Lock>` | `None` by default; set by `for_update(lock)`; kept by `filter`, `order_by`, `window`, `select_related`, `values`; refused together with annotations |

`Lock`: `Wait` (no suffix), `NoWait` (`NOWAIT`), `SkipLocked` (`SKIP LOCKED`).

## ManyToMany (Python)

| Attribute | Meaning |
|---|---|
| `through_ref` | what the developer passed to `through=` (class, name, or `None`) |
| `through_fields` | `(source, target)` foreign key names on the link model, or `None` |
| `through` | the link model class once registered (automatic or custom) |
| `from_name` / `to_name` | link-model foreign keys to the source / target |
| `auto_created` | `through_ref is None` |

States of a custom relation: declared → waiting for the link model (`_PENDING_LINKS`) → linked
(registered in the native registry, reverse accessor installed). Using a manager before it is
linked raises `FieldError` naming the missing link model.

## Model class attributes (Python)

| Attribute | Meaning |
|---|---|
| `_abstract` | `True` when the class's own `Meta` sets `abstract = True` |
| `_fields`, `_relation_fields`, `_many_to_many`, `_defaults` | now include inherited (copied) entries, parents first |

Abstract models: no `_meta`, no table, no `DoesNotExist`; `objects` and instantiation raise
`FieldError`.

## Errors

| Error | When |
|---|---|
| `TransactionError` (new, `ferro.models` / `ferro.db`) | a locking query is evaluated outside `transaction()` |
| `FieldError` | `nowait` with `skip_locked`; `count/exists/aggregate/annotate` on a locking query; invalid link model or `through_fields`; missing required link values; abstract model used; concrete parent |
| `DatabaseUnavailableError` | `nowait=True` meets a locked row (PostgreSQL `55P03`) |
