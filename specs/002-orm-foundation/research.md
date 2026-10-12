# Research: ORM Foundation on SQLite

**Feature**: [spec.md](spec.md) | **Date**: 2026-10-10

## R1. Where the ORM lives (constitution I, III, VI)

- **Decision**: Model metadata, query AST, lookup parsing, value conversion, SQL compilation and
  execution live in Rust. Python keeps the declarative surface: `Model` metaclass, field option
  objects, a thin `QuerySet` wrapper over a Rust `Query` object, and instance methods.
  - `filter(price__gte=1000)` hands the kwargs dict to Rust, which splits `field__lookup`,
    validates against metadata, converts the value to a database value and returns a new
    immutable `Query` (shared AST, copy-on-write). Errors appear at the call site (FR-010).
  - Awaiting a queryset submits the compiled statement once and receives all rows once (FR-018).
- **Rationale**: Constitution I/III require query construction and SQL generation in Rust; doing it
  incrementally per call gives early errors without a second Python-side representation.
- **Alternatives**: Python-side query builder sending a spec on execution (duplicates metadata
  logic in two languages; errors surface late).

## R2. Crate layout

- **Decision**:
  - `crates/database` (`ferro-database`): `Value`, `Statement`, `Rows`, `DbError`, the `Dialect`
    trait and the `Backend` trait (callback-based completion so blocking and async drivers both
    fit).
  - `crates/orm` (`ferro-orm`): `FieldKind`, `FieldMeta`, `ModelMeta`, registry, query AST,
    lookup parsing, compiler (SELECT/COUNT/EXISTS/INSERT/UPDATE/DELETE), DDL (CREATE TABLE/INDEX).
    No Python, no driver: SQL text and parameters are unit-tested directly (SC-002).
  - `crates/sqlite` (`ferro-sqlite`): `SqliteDialect` and `SqliteBackend` (driver + thread pool).
  - `crates/python-bridge`: Python ↔ Rust value conversion, `Query`/`ModelMeta` pyclasses, futures.
- **Rationale**: matches the vision's crate list (orm, database, sqlite), keeps the driver behind
  the backend adapter (constitution II/III), and lets Feature 004 add `crates/postgres` without
  touching the ORM.
- **Alternatives**: one `orm` crate with everything (driver types would leak into the compiler).

## R3. SQLite driver

- **Decision**: `rusqlite` 0.40 with `bundled` (SQLite 3.53.2 compiled in; needs only a C compiler,
  already required by the Rust MSVC toolchain).
- **Rationale**: mature, synchronous, exposes `last_insert_rowid`, `changes`, prepared-statement
  cache and interrupt handles; bundling pins the SQLite version on every platform.
- **Alternatives**: `sqlx` (async facade over a blocking thread anyway, plus its own query layer
  we would not use); system SQLite (version varies per OS).

## R4. Running blocking SQLite without blocking Python (FR-019)

- **Decision**: `SqliteBackend` owns N worker threads (default 4 for files; 1 for `:memory:`, where
  each connection would otherwise be a separate database), each with its own connection. Jobs go
  through a bounded `crossbeam-channel` (capacity 1024, backpressure per constitution rule 7).
  Connections run `PRAGMA journal_mode=WAL` (files), `foreign_keys=ON`, `synchronous=NORMAL`,
  `busy_timeout=5000`.
- **Completion**: the Python side creates an `asyncio.Future` on the running loop when a query is
  submitted. When the job finishes, the worker attaches briefly and calls
  `loop.call_soon_threadsafe(deliver)`; `deliver` runs on the loop thread, converts the Rust rows
  into Python values/instances and resolves the future. A job whose future was cancelled before it
  started is skipped.
- **Rationale**: SQLite is blocking; dedicated threads keep both the event loop and the HTTP
  workers free. Converting on the loop thread avoids contending for the interpreter from DB threads
  (lesson from Feature 001 research R13).
- **Deferred**: interrupting a query that is already executing when its future is cancelled
  (`sqlite3_interrupt`), and batching deliveries per loop; both revisit with benchmarks in 010.

## R5. Value mapping (SQLite)

| Field | Python | Column type | Stored as | Notes |
|---|---|---|---|---|
| String(max_length) | str | `varchar(n)` (TEXT affinity) | TEXT | length checked in Rust |
| Text | str | `text` | TEXT | |
| Integer | int | `integer` | INTEGER | range −2³¹…2³¹−1 checked |
| BigInteger | int | `bigint` | INTEGER | 64-bit range checked |
| Float | float (int accepted) | `real` | REAL | NaN/inf rejected |
| Decimal(d, p) | Decimal (int/str accepted) | `decimal(d,p)` (NUMERIC affinity) | REAL | digits/places checked; read back quantized to `p`; ≤ 15 significant digits exact on SQLite |
| Boolean | bool | `bool` (NUMERIC affinity) | INTEGER 0/1 | |
| Date | date (not datetime) | `date` | TEXT `YYYY-MM-DD` | |
| DateTime | aware datetime | `datetime` | TEXT `YYYY-MM-DD HH:MM:SS.ffffff` in UTC | naive rejected (clarified) |
| Time | time (naive) | `time` | TEXT `HH:MM:SS.ffffff` | |
| UUID | uuid.UUID (str accepted) | `char(36)` | TEXT lower-case hyphenated | |
| JSON | JSON-compatible | `text` | TEXT (Ferro's JSON encoder) | |

Fixed-width text formats keep `ORDER BY` and comparisons correct for dates and times.

- **Bool vs int**: `Integer`/`BigInteger` reject `bool` values; `Boolean` accepts only `bool`.

## R6. Lookup compilation (SQLite)

| Lookup | SQL | Parameter |
|---|---|---|
| exact | `"c" = ?` (`"c" IS NULL` when value is None) | value |
| iexact | `"c" LIKE ? ESCAPE '\'` | escaped value |
| contains / startswith / endswith | `"c" GLOB ?` | `*v*` / `v*` / `*v` with `*?[` bracket-escaped |
| icontains | `"c" LIKE ? ESCAPE '\'` | `%v%` with `%_\` escaped |
| gt / gte / lt / lte | `"c" > ?` … | value |
| in | `"c" IN (?, ?, …)`; empty list → `0 = 1` | values |
| isnull | `"c" IS NULL` / `"c" IS NOT NULL` | none (bool required) |

- `GLOB` is case-sensitive, so `contains` is case-sensitive as in Django on PostgreSQL. SQLite's
  `LIKE` folds ASCII only, so `iexact`/`icontains` are case-insensitive for ASCII letters only on
  SQLite (documented; PostgreSQL in Feature 004 uses full Unicode `ILIKE`/`UPPER`).
- `exclude(...)` compiles to `NOT (...)`; conditions on nullable columns inside a negated group add
  `"c" IS NOT NULL`, so rows with NULL are kept by `exclude` (Django semantics).
- `LIMIT -1 OFFSET n` when only an offset is set (SQLite requires LIMIT before OFFSET).
- Identifiers are double-quoted with `"` doubled; model and field names are validated identifiers.

## R7. Inserts and bulk operations

- **Decision**: single `create`/`save` uses `INSERT ... RETURNING "pk"`. `bulk_create` runs inside
  one `BEGIN IMMEDIATE ... COMMIT` with one cached prepared `INSERT` executed per row, reading
  `last_insert_rowid()` for auto keys; `bulk_update` likewise runs one cached `UPDATE ... WHERE pk`
  per object in one transaction. Any failure rolls the whole batch back.
- **Rationale**: SQLite documents the order of rows produced by a multi-row `RETURNING` as
  unspecified, so mapping returned keys to objects by position is unsafe. Per-row prepared
  statements inside one transaction are fast in SQLite (10,000 rows ≪ 1 s, SC-004).
- `batch_size` bounds statements per transaction chunk only for memory; atomicity covers the
  whole call.

## R8. Errors

- `DbError::Integrity { table, columns, message }` (parsed from SQLite's
  `UNIQUE constraint failed: table.col` / `NOT NULL constraint failed`) → `IntegrityError` naming
  model and field via the registry. `DbError::Operational` (locked, I/O, missing table) →
  `OperationalError`; others → `DatabaseError`. Messages come from SQLite itself, never the Rust
  driver's type names (FR-024).
- "no such table" errors add a hint: run `ferro db create-tables`.

## R9. Registry, re-definition and table names

- Models register on class creation keyed by `module.qualname`. Re-defining the same class (module
  reload, tests defining models inside functions) replaces the earlier entry; a *different* class
  mapping to an existing table raises `FieldError` naming both (FR-005).
- Default table name: snake_case of class name (clarified). `Meta.table` must be an identifier.
- Reserved names: `objects`, `pk`, `save`, `delete`, `refresh`, `to_dict`, `DoesNotExist`,
  `MultipleObjectsReturned`, `Meta`, names starting with `_`.

## R10. Instances

- Fields are plain class attributes (`Product.price` is the field object); instance values live in
  the instance `__dict__`, so attribute access is a normal dict lookup (no descriptor overhead).
- Rows become instances in Rust via `cls.__new__(cls)` plus direct `__dict__` population, with no
  per-row Python function calls (constitution VI). `_persisted = True` marks stored rows.
- Defaults are applied in `Model.__init__` (value or zero-arg callable); unknown keyword arguments
  raise `FieldError`.

## R11. Database configuration and lifecycle

- `database_url` becomes a Ferro setting (`FERRO_DATABASE_URL`, `--database-url`, `App(...)`).
  `ServerRunner` connects before serving and disconnects after shutdown. Scripts/tests use
  `await ferro.db.connect(url)` / `await ferro.db.disconnect()`; one global "default" database.
- URL forms: `sqlite:///rel.db` (relative to cwd), `sqlite:////abs.db`, `sqlite:///C:/x.db`
  (Windows), `sqlite:///:memory:`. Unknown schemes → `ConfigurationError` listing supported ones.
