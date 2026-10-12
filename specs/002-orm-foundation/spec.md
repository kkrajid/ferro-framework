# Feature Specification: ORM Foundation on SQLite

**Feature Branch**: `002-orm-foundation`

**Created**: 2026-10-10

**Status**: Draft

**Input**: User description: "Feature 002 from docs/roadmap.md — Ferro's own ORM on SQLite
(product vision Phase 3, §5–7, §11). Developers declare models with a Django-like field system,
query them with an async QuerySet (filter/exclude/order/limit, lookups), create/update/delete
single and bulk records, and get values back with correct Python types. The ORM is framework-owned:
Python model API → model metadata → query representation → SQL compiler → database backend, with
parameters always bound. Relations, transactions, PostgreSQL and migrations are later features."

## Clarifications

### Session 2026-10-10

- Q: How are tables created before the migration engine (Feature 005) exists? → A:
  `ferro db create-tables` / `await ferro.db.create_tables()` create missing tables and indexes
  only; existing tables are never altered (FR-022).
- Q: What is a model's default table name? → A: snake_case of the class name (`OrderItem` →
  `order_item`), overridable with `class Meta: table = "..."` (FR-005).
- Q: How are naive datetimes handled by `DateTime` fields? → A: rejected with a field error; aware
  values are stored in UTC and read back as UTC-aware (US4 scenario 7, FR-006).

## User Scenarios & Testing *(mandatory)*

Actor: the **application developer** using Ferro (see Feature 001). Secondary actor: the
**database**, an SQLite file (or in-memory database) the application owns.

### User Story 1 - Declare a model and store records (Priority: P1)

A developer declares a model class with typed fields, points the application at an SQLite
database, creates the table for it, and creates and reads back records.

```python
from ferro import models

class Product(models.Model):
    name = models.String(max_length=200)
    price = models.Decimal(max_digits=10, decimal_places=2)
    stock = models.Integer(default=0)
    active = models.Boolean(default=True)

product = await Product.objects.create(name="Laptop", price=Decimal("75000.00"), stock=10)
same = await Product.objects.get(id=product.id)
```

**Why this priority**: This is the product's second milestone (vision §25): proving the owned ORM.
Nothing else in the ORM is usable without declaring models and persisting them.

**Independent Test**: Declare the model above, connect to a fresh SQLite database, create the
table, create a record, read it back by primary key, and compare every field value and type.

**Acceptance Scenarios**:

1. **Given** the `Product` model without an explicit primary key, **When** the table is created,
   **Then** it has an auto-incrementing integer primary key `id` plus one column per field.
2. **Given** an empty table, **When** `await Product.objects.create(name="Laptop", price=...)` runs,
   **Then** the returned instance has an `id`, `stock == 0` and `active is True` (defaults), and
   the row exists in the database.
3. **Given** a stored product, **When** `await Product.objects.get(id=...)` runs, **Then** every
   attribute equals what was stored, with Python types `int`, `str`, `Decimal`, `bool`, etc.
4. **Given** an instance, **When** the developer changes attributes and calls `await product.save()`,
   **Then** the row is updated; **When** they call `await product.delete()`, **Then** the row is gone.
5. **Given** a value that violates a field rule (string longer than `max_length`, `None` for a
   non-nullable field, wrong type), **When** saving, **Then** a field error names the model, field
   and problem, and nothing is written.

---

### User Story 2 - Query with filters, ordering and pagination (Priority: P1)

A developer retrieves records with Django-style lookups, excludes, ordering, limits and offsets,
counts and existence checks, all awaited.

```python
cheap = await Product.objects.filter(price__lt=1000, active=True).order_by("-price")
page = await Product.objects.order_by("name")[20:40]
n = await Product.objects.exclude(stock=0).count()
```

**Why this priority**: Reading data selectively is the core of every business application.

**Independent Test**: Seed a table with known rows; run each lookup and combination; compare the
returned records to the expected set and order.

**Acceptance Scenarios**:

1. **Given** seeded products, **When** filtering with each lookup — `exact`, `iexact`, `contains`,
   `icontains`, `startswith`, `endswith`, `gt`, `gte`, `lt`, `lte`, `in`, `isnull` — **Then** exactly
   the matching records are returned.
2. **Given** chained `filter(...)` and `exclude(...)` calls, **Then** conditions combine with AND,
   and each `exclude` negates its own group of conditions.
3. **Given** `order_by("-price", "name")`, **Then** records come back in that order; ordering by an
   unknown field raises a field error naming it.
4. **Given** slicing `[20:40]`, `limit(n)` / `offset(n)`, **Then** the corresponding window is
   returned; negative indexes are rejected with a clear error.
5. **Given** a queryset, **When** awaiting `count()`, `exists()`, `first()`, `last()`, **Then** they
   return an `int`, a `bool`, the first/last record by the queryset's ordering (by primary key when
   unordered) or `None`.
6. **Given** `get(...)` matching no record or several records, **Then** `Product.DoesNotExist` or
   `Product.MultipleObjectsReturned` is raised respectively; `get_or_404(...)` turns "no record"
   into an HTTP 404 response when used in a handler.
7. **Given** a lookup on an unknown field or an unknown lookup name, **When** the queryset is built,
   **Then** a field error names the field or lookup and lists valid options.
8. **Given** user-supplied text containing SQL syntax (e.g. `"x'; DROP TABLE product; --"`) used as a
   filter value, **Then** it is treated as plain data and the table is unaffected.

---

### User Story 3 - Bulk and set-based writes (Priority: P2)

A developer updates or deletes many rows with one statement and inserts or updates many objects at
once.

```python
await Product.objects.filter(stock=0).update(active=False)
await Product.objects.filter(active=False).delete()
await Product.objects.bulk_create([Product(name=f"Item {i}", price=10) for i in range(500)])
await Product.objects.bulk_update(products, fields=["price"])
```

**Why this priority**: Real applications import and maintain data in bulk; doing it row by row is
slow, but single-row CRUD (Story 1) already delivers value first.

**Independent Test**: Seed rows, run each bulk operation, and verify affected counts and resulting
table contents.

**Acceptance Scenarios**:

1. **Given** a filtered queryset, **When** awaiting `.update(field=value, ...)`, **Then** all matching
   rows change in one statement and the number of affected rows is returned.
2. **Given** a filtered queryset, **When** awaiting `.delete()`, **Then** matching rows are removed and
   the count is returned; `Product.objects.all().delete()` empties the table.
3. **Given** 500 unsaved instances, **When** awaiting `bulk_create(...)`, **Then** all rows are
   inserted, every instance gets its primary key, and the operation is all-or-nothing.
4. **Given** modified instances, **When** awaiting `bulk_update(objs, fields=[...])`, **Then** only the
   listed fields are written for each object.
5. **Given** an `update()` naming an unknown field or an invalid value, **Then** a field error is
   raised before anything is written.

---

### User Story 4 - The full field system (Priority: P2)

A developer uses every MVP field type with options for primary keys, defaults, nullability,
uniqueness and indexes, and values round-trip with correct Python types.

**Why this priority**: Business applications need dates, decimals, UUIDs and JSON; constraints
protect data integrity.

**Independent Test**: Declare a model with every field type and option; store and read back edge
values for each; verify constraint violations.

**Acceptance Scenarios**:

1. **Given** fields `String`, `Text`, `Integer`, `BigInteger`, `Float`, `Decimal`, `Boolean`,
   `Date`, `DateTime`, `Time`, `UUID`, `JSON`, **When** values are stored and read back, **Then** they
   compare equal with types `str`, `str`, `int`, `int`, `float`, `Decimal`, `bool`,
   `datetime.date`, `datetime.datetime`, `datetime.time`, `uuid.UUID`, and JSON-compatible values.
2. **Given** `null=True`, **Then** `None` is stored and read back; without it `None` is rejected.
3. **Given** `default=value` or `default=callable` (e.g. `uuid.uuid4`), **Then** unset fields receive
   the default at instance creation.
4. **Given** `unique=True`, **When** a duplicate value is saved, **Then** an integrity error naming
   the model and field is raised and the original row is unchanged.
5. **Given** `index=True`, **Then** an index exists on that column after table creation.
6. **Given** a field declared `primary_key=True` (e.g. a `UUID` with `default=uuid.uuid4`), **Then**
   no automatic `id` is added and `pk` refers to that field.
7. **Given** a `DateTime` value, **Then** timezone-aware values are stored in UTC and read back as
   UTC-aware datetimes; naive datetimes are rejected with a field error.

---

### User Story 5 - Use the ORM inside a running application (Priority: P2)

A developer configures the database once for the application and uses models inside handlers; the
server never stalls while queries run.

```python
app = App(database_url="sqlite:///shop.db")

@app.get("/products/{id}")
async def product(id: int):
    p = await Product.objects.get_or_404(id=id)
    return p.to_dict()
```

**Why this priority**: The ORM exists to serve web requests; this proves the HTTP runtime and the
ORM work together (vision §21 building block).

**Independent Test**: Run an app with `database_url`, create tables with `ferro db create-tables`,
call create/list/detail endpoints concurrently, and verify responses and that a slow query does
not block other requests.

**Acceptance Scenarios**:

1. **Given** `database_url` set via `App(...)`, `FERRO_DATABASE_URL` or `--database-url`, **When** the
   server starts, **Then** the ORM uses that database (same precedence as other settings).
2. **Given** an application module with models, **When** the developer runs
   `ferro db create-tables`, **Then** missing tables and indexes are created, existing ones are left
   untouched, and the created/skipped tables are listed.
3. **Given** a handler that queries the database, **When** many requests arrive concurrently,
   **Then** all succeed and query execution never runs on the Python execution thread.
4. **Given** model instances, **When** the handler returns `instance.to_dict()` (or a list of
   them), **Then** the response JSON contains every field with JSON-friendly values.
5. **Given** no database is configured, **When** a query runs, **Then** a configuration error says
   how to set `database_url`.

### Edge Cases

- Two models mapping to the same table name: startup/registration error naming both models.
- Field names that clash with ORM API (`objects`, `pk`, `save`, `delete`, `DoesNotExist`) or SQL
  keywords (`order`, `select`): API clashes rejected at class creation; SQL keywords work because
  identifiers are always quoted.
- Empty `__in=[]`: returns no rows (no SQL error).
- `filter(field=None)` behaves like `isnull=True`.
- `contains`/`startswith`/`endswith` values containing `%` or `_` match literally.
- Very large integers outside 64-bit range: field error, not silent truncation.
- `Decimal` values with more digits than `max_digits`/`decimal_places` allow: field error.
- JSON values that are not JSON-serializable: field error naming the field.
- Concurrent writes to the same SQLite file from many requests: serialized by the database layer
  without "database is locked" errors under normal load.
- Database file path not writable: clear error at connect time.
- Queryset reuse: awaiting the same queryset twice runs the query twice (no stale cache surprises).

## Requirements *(mandatory)*

### Functional Requirements

**Models & fields**

- **FR-001**: Developers MUST declare models by subclassing `ferro.models.Model` with class
  attributes created from field types; every model is registered when its class is created.
- **FR-002**: The MVP field types MUST be `String(max_length)`, `Text`, `Integer`, `BigInteger`,
  `Float`, `Decimal(max_digits, decimal_places)`, `Boolean`, `Date`, `DateTime`, `Time`, `UUID`,
  `JSON`.
- **FR-003**: Every field MUST support `null` (default False), `default` (value or zero-argument
  callable), `unique` (default False), `index` (default False), `primary_key` (default False) and
  `column` (custom column name).
- **FR-004**: A model without a `primary_key=True` field MUST get an auto-incrementing 64-bit integer
  primary key named `id`. `instance.pk` MUST always refer to the primary key value.
- **FR-005**: The table name MUST default to the snake_case class name and be overridable with an
  inner `class Meta: table = "..."`; duplicate table names and field names clashing with the model
  API MUST be rejected when the class is created.
- **FR-006**: Values MUST be validated before writing (type, `max_length`, digits/places, nullability,
  integer range, JSON-serializability, timezone awareness) and violations MUST raise
  `ferro.models.FieldError` naming model, field and problem.

**Querying**

- **FR-007**: Each model MUST expose `Model.objects` with `all`, `filter`, `exclude`, `order_by`,
  `limit`, `offset`, slicing, `get`, `get_or_404`, `first`, `last`, `count`, `exists`, `create`,
  `update`, `delete`, `bulk_create`, `bulk_update`.
- **FR-008**: Querysets MUST be lazy and immutable (each call returns a new queryset) and execute only
  when awaited (or when a terminal method is awaited); awaiting a queryset returns a `list` of
  instances.
- **FR-009**: Lookups MUST include `exact`, `iexact`, `contains`, `icontains`, `startswith`,
  `endswith`, `gt`, `gte`, `lt`, `lte`, `in`, `isnull`, expressed as `field__lookup=value`
  (`field=value` means `exact`; `field=None` means `isnull=True`).
- **FR-010**: Unknown fields, lookups, or ordering names MUST raise `FieldError` when the queryset is
  built, listing valid names.
- **FR-011**: `get` MUST raise `Model.DoesNotExist` / `Model.MultipleObjectsReturned`;
  `get_or_404` MUST raise an HTTP 404 (`HTTPException`) instead of `DoesNotExist`.
- **FR-012**: Unordered `first()`/`last()` MUST order by primary key.

**Writing**

- **FR-013**: `create(**values)`, `instance.save()`, `instance.delete()` and `instance.refresh()` MUST
  be available; `save()` inserts when the instance has no stored primary key and updates otherwise.
- **FR-014**: `queryset.update(**values)` and `queryset.delete()` MUST execute as single statements
  and return the number of affected rows.
- **FR-015**: `bulk_create(objs, batch_size=None)` MUST insert all objects atomically and set their
  primary keys; `bulk_update(objs, fields, batch_size=None)` MUST write only the listed fields,
  atomically.
- **FR-016**: Unique-constraint violations MUST raise `ferro.models.IntegrityError` naming the model
  and field(s).

**Safety & architecture**

- **FR-017**: Every value MUST reach the database as a bound parameter; identifiers MUST come from
  model metadata and be quoted. No API may accept raw SQL fragments in this feature.
- **FR-018**: Query construction, SQL generation, value conversion and execution MUST happen in the
  framework core, not in Python; a query MUST cross between Python and the core once to submit and
  once to deliver results (not per row).
- **FR-019**: Query execution MUST NOT run on the Python execution thread or block it; while a query
  runs, other requests and coroutines proceed.
- **FR-020**: The ORM MUST be usable outside the web server (scripts, tests) via
  `await ferro.db.connect(url)` and `await ferro.db.disconnect()`.

**Database & schema**

- **FR-021**: The database MUST be configured with `database_url` (`sqlite:///relative/path.db`,
  `sqlite:////absolute/path.db`, `sqlite:///:memory:`), settable through `App(...)`,
  `FERRO_DATABASE_URL` or `--database-url` with the Feature 001 precedence.
- **FR-022**: `await ferro.db.create_tables()` and `ferro db create-tables [TARGET]` MUST create
  missing tables and indexes for all registered models and leave existing tables unchanged, until
  the migration engine (Feature 005) supersedes them.
- **FR-023**: The SQLite connection MUST enable foreign-key enforcement and write-ahead logging (file
  databases), and wait (busy timeout, default 5 s) instead of failing immediately when the file is
  locked.
- **FR-024**: Database errors MUST surface as `ferro.models.DatabaseError` (or subclasses) whose
  messages do not name the underlying driver library.

**Developer experience**

- **FR-025**: `instance.to_dict()` MUST return a dict of field values ready for JSON responses; model
  instances MUST have a readable `repr`.
- **FR-026**: Public ORM classes MUST have type hints and docstrings, and `examples/` MUST include a
  runnable CRUD API example using the ORM.

### Key Entities

- **Model**: a developer class mapping to one table; has metadata (name, table, fields, primary key).
- **Field**: a typed column definition with options (null, default, unique, index, primary_key,
  column).
- **Model registry**: all models known to the process, with their metadata; detects table clashes.
- **QuerySet**: an immutable description of a query (model, conditions, ordering, window) executed
  on await.
- **Condition**: field + lookup + value, combined with AND; exclude groups are negated.
- **Database**: the configured connection target (URL, pool, options).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A Django developer can write a model, create its table and perform create/read/update/
  delete within 10 minutes using only the getting-started guide.
- **SC-002**: Every lookup and write operation in this spec is verified by automated tests that
  assert the generated SQL text and its bound parameters, plus the database results.
- **SC-003**: Attempted SQL injection through every filter, update, create and ordering input leaves
  the database unchanged in 100% of the injection test cases.
- **SC-004**: Reading 1,000 rows of a 6-field model takes under 50 ms end to end on a development
  machine (release build), and `bulk_create` of 10,000 rows completes in under 1 second.
- **SC-005**: With 100 concurrent clients calling a list endpoint backed by SQLite, there are zero
  failed requests and a non-database route keeps p99 latency under 50 ms.
- **SC-006**: All MVP field types round-trip their edge values (min/max integers, 0, empty strings,
  non-ASCII text, max-precision decimals, UTC datetimes with microseconds, nested JSON) with
  100% equality.

## Assumptions

- Relations (`ForeignKey`, `OneToOne`, `ManyToMany`, cross-relation lookups), transactions,
  aggregation (`Sum`, `Avg`, ...), `Q`/`F` expressions, `select_related`/`prefetch_related` and
  `values()` are delivered by later features (003 and beyond).
- PostgreSQL is Feature 004; the backend abstraction is designed for it now but only SQLite ships.
- Schema changes to existing tables (add/alter/drop columns) need the migration engine
  (Feature 005); `create-tables` only creates what is missing.
- The ORM API is async-only in this feature; plain `def` handlers cannot call it directly.
- SQLite stores `Decimal` with numeric affinity and values are quantized to `decimal_places` when
  read, so comparisons are numeric; exact arbitrary-precision storage arrives with PostgreSQL.
- `DateTime` requires timezone-aware values (stored as UTC); this avoids ambiguous local times.
- One database per process ("default"); multiple databases are out of MVP scope.
