# Research: Relationships, Transactions & Aggregation

**Feature**: [spec.md](spec.md) | **Date**: 2026-10-10

## R1. Relation metadata lives in the Rust registry

- **Decision**: `FieldSpec`/`FieldMeta` gain `relation: Option<Relation>` (target qualname, target
  table and key column, one-to-one flag, `OnDelete`, related name) and `attname` (`category_id`).
  A relation field's kind is the target primary key's kind, so value conversion, DDL and
  migrations reuse the existing code. `ModelMeta::new` takes a resolver for targets (`"self"`
  resolves to the model being built). `ModelMeta.indexes` holds composite indexes
  (`unique_together`). The `Registry` keeps many-to-many relations and computes reverse relations
  (query name → source model/field or link table) when a model is registered.
- **Rationale**: lookups, joins and migrations are compiled in Rust (constitution I, III); one
  place resolves names.
- **Alternatives rejected**: relation metadata only in Python (every lookup would cross the boundary
  twice); a new `FieldKind::ForeignKey` (every match on kinds would need a relation case).

## R2. Python declares, defers and wires relations

- `ForeignKey`/`OneToOne` are data descriptors (`category`) plus an id descriptor (`category_id`)
  that drops the cached object when the key changes. Caches live in `instance.__dict__["_related"]`.
- A string target that is not defined yet parks the model in a pending list; it is registered
  (meta, manager, reverse accessors) as soon as all targets exist. Until then `Model.objects`
  raises `FieldError` naming the missing target.
- `ManyToMany` creates a hidden link model class (`Product_tags`, table `product_tags`, fields
  `product`/`tag` with `related_name="+"`, `unique_together`) after the source registers; the
  registry records the relation. Self references use `from_<model>`/`to_<model>`.
- Reverse accessors: `<model>_set` / `related_name` manager (foreign key, many-to-many),
  single-object descriptor (one-to-one). `related_name="+"` installs none.

## R3. Lookups across relations: correlated EXISTS

- **Decision**: a condition whose path crosses a relation compiles to
  `EXISTS (SELECT 1 FROM <target> AS "T1" WHERE <correlation> AND <conditions>)`, nested for further
  hops. In one AND node (one `filter()` call or one `Q` conjunction), conditions sharing the first
  hop share the subquery (same related row); separate calls produce separate subqueries.
  Correlations: forward `T.pk = outer.fk`; reverse `T.fk = outer.pk`; many-to-many
  `FROM link AS J JOIN target AS T ON T.pk = J.to WHERE J.from = outer.pk`.
- Shortcuts: `category=…`, `category_id=…`, `category__pk=…`, `category__in`, `category__isnull`
  compile to the local column (no subquery).
- **Rationale**: no duplicate rows (no `DISTINCT` needed), exact `exclude()` semantics with NULL
  keys (`NOT EXISTS`), same SQL on both databases; PostgreSQL plans EXISTS as a semi-join and
  SQLite probes the indexed key per row.
- **Alternatives rejected**: JOIN + DISTINCT (duplicates, wrong `exclude()` with NULLs, conflicts
  with aggregates).

## R4. Joins only where rows must be widened

- `select_related`, `order_by("category__name")`, `values("category__name")` and aggregates over
  forward paths use `LEFT JOIN "category" AS "T1" ON "T1"."id" = "product"."category_id"`, one
  alias per distinct forward path. Columns are qualified only when a query has joins, so SQL of
  relation-free queries is unchanged (SC-005).
- Multi-valued paths are refused in `select_related`, `order_by`, `values` and `aggregate()` with a
  message pointing to `prefetch_related` / `annotate`.

## R5. Condition tree, Q and NULL semantics

- `Query.filters: Vec<Node>`; `Node = Leaf | And | Or | Not`. `filter(**kw)` adds `And(leaves)`,
  `exclude(**kw)` adds `Not(And(leaves))`; `Q` objects convert to the same nodes.
- A comparison leaf on a nullable column under an odd number of `NOT`s gets
  `AND col IS NOT NULL`, so `NOT` of an unknown comparison keeps NULL rows (existing Feature 002
  behaviour, generalised). EXISTS is two-valued, so subqueries reset the parity.
- Rendering keeps today's SQL for keyword-only filters.

## R6. Aggregates and annotations

- `Aggregate { func, path, distinct, default }`.
- `aggregate()`: `SELECT agg AS "a", ... FROM root [LEFT JOIN forward paths] WHERE ...`; a sliced
  query is wrapped in a subquery.
- Per-record `annotate()` (no `values()`): the path must cross a multi-valued relation; compiled
  as a correlated scalar subquery `(SELECT SUM(T2.price) FROM order_item T1 JOIN product T2 ON ...
  WHERE T1.order_id = "order"."id")`. Filters on it compile to WHERE, ordering by alias.
- Grouped (`values(...).annotate(...)`): `SELECT values..., aggs FROM root LEFT JOIN ... WHERE ...
  GROUP BY values... HAVING <filters on annotations> ORDER BY ...`.
- Result types (clarified): `Count` int; `Avg` float; `Sum/Min/Max` the field's type (PostgreSQL
  `SUM(bigint)` returns numeric → converted back to int). `default=` → `COALESCE(agg, ?)`.

## R7. F expressions

- `Expr = Field(local field) | Value | Binary(op, l, r)` with `+ - * /`; numeric fields only for
  arithmetic. Used as lookup operands (`"price" > "cost"`) and update values
  (`"stock" = "stock" - ?`). Relation paths in `F()` are rejected (out of scope).

## R8. Transactions: a reserved connection per task tree

- **Decision**: `Job::Session(receiver)` reserves a pooled connection: the worker that takes it
  runs only jobs from the session channel (`async_channel`, usable blocking on SQLite threads and
  async on Tokio) until every sender is dropped, then rolls back anything left open and returns
  to the pool. `Job::Transaction(Begin | Commit | Rollback | Savepoint | Release | RollbackTo)`
  carries control statements; savepoint names are generated (`ferro_sp_N`).
- The bridge's `submit()` reads a context variable (`ferro._ferro.transaction_context`); when a
  session is active the job goes to the session channel. Child tasks inherit the context; other
  requests never see it.
- `ferro.db.transaction()`: outermost block → new session + `BEGIN`; nested → savepoint;
  exit → commit/release or rollback/rollback-to; `close()` in `finally`. Cancelled awaits are
  covered because a dropped session always rolls back on the worker.
- SQLite: `BEGIN IMMEDIATE` (clarified assumption); PostgreSQL: `BEGIN` (read committed). Batches
  inside a session use savepoints instead of a nested transaction.
- **Alternatives rejected**: pinning by `asyncio.Task` identity (child tasks would escape), holding
  a driver transaction object across jobs (borrows the connection), pyo3-async-runtimes (not
  needed; constitution v1.1.0 marks it optional).

## R9. Foreign keys in DDL and migrations

- Column DDL: `"category_id" bigint NOT NULL REFERENCES "category" ("id") ON DELETE CASCADE`
  (PostgreSQL names it `<table>_<column>_fkey`). `PROTECT` → `RESTRICT`, `DO_NOTHING` →
  `NO ACTION`.
- `ColumnDef.references: Option<ForeignKeyDef { table, column, on_delete }>`; migration files
  render `ops.Column("category_id", "BigInteger", references="category.id", on_delete="cascade")`.
  `IndexDef` gains `columns` and `unique` (`ops.CreateIndex("t", ["a", "b"], name, unique=True)`;
  the single-column form is unchanged).
- Diff: new tables topologically sorted by references (self references ignored; cycles → error);
  dropped tables reverse-sorted; composite indexes from `ModelMeta.indexes`.
- PostgreSQL alters: changed reference → `DROP CONSTRAINT "<t>_<c>_fkey"` + `ADD CONSTRAINT ...
  FOREIGN KEY`; `RenameTable`/`RenameColumn` also rename `_fkey`/`_key` constraints and
  default-named indexes so later operations find them.
- SQLite: new `Job::Schema(statements)`: `PRAGMA foreign_keys = OFF` (outside the transaction),
  `BEGIN IMMEDIATE`, statements, `PRAGMA foreign_key_check` (rows → error + rollback), `COMMIT`,
  `PRAGMA foreign_keys = ON` — SQLite's documented procedure; rebuilding a referenced table then
  neither cascades nor breaks references. Migrations and `create-tables` use `Job::Schema`;
  inside `transaction()` it is refused.

## R10. Delete rules and errors

- Deletes rely on `ON DELETE` (clarified). A foreign-key violation raised by a delete is reported
  as `ProtectedError` (subclass of `IntegrityError`) naming the model.
- `bulk_create(ignore_conflicts=True)` → `INSERT ... ON CONFLICT DO NOTHING` (SQLite ≥ 3.24,
  PostgreSQL); used by many-to-many `add()`.
