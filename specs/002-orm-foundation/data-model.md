# Data Model: ORM Foundation on SQLite

Runtime entities of the ORM. Rust names in `crates/*`, Python names in `python/ferro/`.

## Value (`ferro_database::Value`)

`Null | Integer(i64) | Real(f64) | Text(String) | Blob(Vec<u8>)`. Every bound parameter and every
cell read back is a `Value`. Field-specific meaning comes from `FieldKind` (research R5).

## FieldKind / FieldMeta (`ferro_orm`)

| Kind | Options | Python field class |
|---|---|---|
| `String { max_length }` | max_length 1…10,485,760 | `models.String(max_length=...)` |
| `Text` | — | `models.Text()` |
| `Integer` | — | `models.Integer()` |
| `BigInteger` | — | `models.BigInteger()` |
| `Float` | — | `models.Float()` |
| `Decimal { max_digits, decimal_places }` | 1 ≤ max_digits ≤ 38, 0 ≤ places ≤ max_digits | `models.Decimal(max_digits=..., decimal_places=...)` |
| `Boolean` | — | `models.Boolean()` |
| `Date`, `DateTime`, `Time` | — | `models.Date()`, `models.DateTime()`, `models.Time()` |
| `Uuid` | — | `models.UUID()` |
| `Json` | — | `models.JSON()` |

`FieldMeta { name, column, kind, null, unique, index, primary_key, auto }`.
Common options (all fields): `null=False`, `default=<value|callable>`, `unique=False`,
`index=False`, `primary_key=False`, `column=None`. `primary_key` implies `unique` and `not null`.
`auto` is set only for the implicit `id` (`BigInteger`, auto-increment).

Validation at registration: field and column names are identifiers and unique; exactly one primary
key (implicit `id` added if none); `index` ignored for primary keys; Decimal digit bounds.

## ModelMeta and Registry (`ferro_orm`)

`ModelMeta { qualname, label (class name), table, fields: Vec<FieldMeta>, pk: usize }`.

`Registry` maps `qualname → Arc<ModelMeta>` and `table → qualname`:
- same qualname registered again → replaced (re-definition);
- different qualname with an existing table → `FieldError("tables clash: a.Product and b.Item both use table 'product'")`.

Reserved attribute names (rejected as field names): `objects`, `pk`, `save`, `delete`, `refresh`,
`to_dict`, `DoesNotExist`, `MultipleObjectsReturned`, `Meta`, and any name starting with `_`.

## Query AST (`ferro_orm::query`)

```text
Query { model: Arc<ModelMeta>, groups: Vec<Group>, ordering: Vec<Order>, limit: Option<u64>, offset: Option<u64> }
Group { negated: bool, conditions: Vec<Condition> }        # one per filter()/exclude() call
Condition { field: usize, lookup: Lookup, operand: Operand }
Lookup = Exact | IExact | Contains | IContains | StartsWith | EndsWith | Gt | Gte | Lt | Lte | In | IsNull
Operand = Value(Value) | List(Vec<Value>) | Flag(bool)
Order { field: usize, descending: bool }
```

Lookup applicability: text lookups (`iexact`, `contains`, `icontains`, `startswith`, `endswith`)
only on `String`/`Text`; comparisons (`gt`…`lte`) on all kinds except `Boolean` and `Json`; `exact`,
`in`, `isnull` on all kinds. `field=None` → `IsNull(true)`. `in` requires a list/tuple/set.

Window arithmetic: slicing `[a:b]` on a queryset already windowed composes offsets/limits;
`update()`/`delete()` on a windowed queryset raise `FieldError` (as Django does).

## Database (`python-bridge::db`)

`Database { url, backend: Arc<dyn Backend>, dialect: &'static dyn Dialect }`, one global default.
States: `disconnected → connected → disconnected`. Queries while disconnected raise
`ConfigurationError("no database configured; set database_url ...")`.

## Job / Outcome (`ferro_database`)

```text
Job = Query(Statement) → Rows
    | Execute(Statement) → Affected(u64)
    | Batch { statements, collect_rowids } → Batch(Vec<i64>)   # one transaction
    | ListTables → Tables(Vec<String>)
DbError = Integrity { kind: Unique|NotNull|ForeignKey|Check|Other, table, columns, message }
        | Operational { message } | Closed | Other { message }
```

## Python exceptions (`ferro.models`)

```text
FieldError(Exception)                 # invalid model, field, lookup, value
DatabaseError(Exception)
├── IntegrityError(DatabaseError)     # .model, .fields
└── OperationalError(DatabaseError)
ObjectDoesNotExist(Exception)  ─▶ Model.DoesNotExist (per model)
MultipleObjectsReturned(Exception) ─▶ Model.MultipleObjectsReturned (per model)
```

## Instance state

Instance `__dict__` holds one entry per field name plus `_persisted: bool`. `pk` is a property
reading the primary-key field. `save()`: not persisted → INSERT (sets pk, `_persisted = True`);
persisted → UPDATE by pk, falling back to INSERT when no row matched. `delete()`: DELETE by pk,
sets `_persisted = False` and an auto pk back to `None`.
