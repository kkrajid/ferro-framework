# Data Model: Relationships, Transactions & Aggregation

## Field metadata (Rust `ferro_orm`)

| Entity | Fields | Rules |
|---|---|---|
| `OnDelete` | `Cascade`, `SetNull`, `Protect`, `DoNothing` | `SetNull` requires `null=True` |
| `Relation` | `target` (qualname), `target_table`, `target_column`, `one_to_one`, `on_delete`, `related_name: Option` | target must be registered or `"self"` |
| `FieldMeta` (+) | `attname`, `relation: Option<Relation>` | relation field: `attname = name + "_id"`, default column = attname, kind = target pk kind, `index` unless unique; may not be a primary key |
| `CompositeIndex` | `name`, `columns`, `unique` | from `Meta.unique_together`; name `<table>_<c1>_<c2>_uniq` |
| `ManyToMany` | `source`, `name`, `target`, `through` (qualnames), `from_field`, `to_field`, `related_name` | link table `<source table>_<name>` |
| `ReverseRelation` | `query_name`, `kind` (`ForeignKey`/`OneToOne` via source field, `ManyToMany` via link) | query names unique per model and distinct from field names |

## Query AST (Rust)

| Entity | Fields |
|---|---|
| `Hop` | `Forward{field}` / `Reverse{source, field}` / `ManyToMany{through, from_field, to_field, target}`; resolved `Arc<ModelMeta>`s |
| `Target` | `Field { hops, model, field }` or `Annotation(index)` |
| `Expr` | `Field(index)`, `Value`, `Binary(op, Expr, Expr)` |
| `Condition` | `target`, `lookup`, `operand` (`Value`, `List`, `Flag`, `Expr`) |
| `Node` | `Leaf(Condition)`, `And(Vec)`, `Or(Vec)`, `Not(Box)` |
| `Aggregate` | `func` (Count/Sum/Avg/Min/Max), `path` (hops + field or `*`), `distinct`, `default` |
| `Query` (+) | `filters: Vec<Node>`, `annotations: Vec<(name, Aggregate)>`, `values: Option<Vec<Selected>>` + `flat`/`tuples`, `related: Vec<Vec<Hop>>` (select_related) |

## Database jobs (Rust `ferro_database`)

| Job | Meaning |
|---|---|
| `Session(receiver)` | reserve a connection; run jobs from the channel until closed; roll back leftovers |
| `Transaction(Begin \| Commit \| Rollback \| Savepoint(n) \| Release(n) \| RollbackTo(n))` | control statements on a session |
| `Schema(statements)` | DDL batch; SQLite disables FK enforcement around it and checks integrity before commit |
| `Batch{..., ignore_conflicts}` | unchanged semantics; uses a savepoint inside a session |

## Python objects

| Object | Responsibility |
|---|---|
| `ForeignKey` / `OneToOne` | descriptor for the related object; `<name>_id` descriptor; spec with relation |
| `ManyToMany` | creates the hidden link model; descriptor returning a `ManyRelatedManager` |
| `RelatedManager` | reverse foreign key manager (queryset methods + `create`) |
| `ManyRelatedManager` | `add/remove/clear/set` + queryset methods, both directions |
| `Q`, `F`, `Count/Sum/Avg/Min/Max` | expression objects converted by the bridge |
| `transaction` | async context manager/decorator over a native `Session` |
| Exceptions | `RelatedNotLoaded(FieldError)`, `ProtectedError(IntegrityError)` |

## State transitions: session

`created → (Begin ok) open → [savepoints push/pop] → Commit|Rollback → closed`; any state →
`closed` when dropped (worker rolls back if a transaction is still open).
