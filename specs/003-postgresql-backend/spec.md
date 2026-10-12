# Feature Specification: PostgreSQL Backend

**Feature Branch**: `003-postgresql-backend`

**Created**: 2026-10-10

**Status**: Draft

**Input**: User description: "Feature 003 from docs/roadmap.md (reordered at the user's request so
PostgreSQL and migrations come before relations). Ferro's ORM works on PostgreSQL with the same
models and the same QuerySet API as SQLite: `database_url = postgresql://...`, connection pooling,
exact decimals, native date/time/UUID/JSON types, Unicode-aware case-insensitive lookups, clear
errors, and every ORM test running on both SQLite and PostgreSQL (vision Phase 4, §4, §11)."

## Clarifications

### Session 2026-10-10

The user asked to continue without interruptions; the recommended option was taken for each
open question and recorded here.

- Q: Should TLS ship in this feature? → A: Yes. `sslmode=prefer` (default), `require`,
  `verify-full` encrypt and always verify the server certificate (public roots or `sslrootcert`);
  `disable` turns TLS off. Verification is stricter than libpq's `require` (secure by default).
- Q: How do the PostgreSQL tests get a server? → A: They start a temporary local cluster (trust
  authentication on 127.0.0.1, random port, removed afterwards) when PostgreSQL server binaries
  are installed; otherwise they use `FERRO_TEST_POSTGRES_URL`; otherwise they are skipped with a
  visible reason. Each test gets its own schema.
- Q: Ordering of NULLs differs between databases (PostgreSQL sorts NULLs last ascending, SQLite
  first). → A: Keep each database's native behavior and document it, as Django does.

## User Scenarios & Testing *(mandatory)*

Actors: the **application developer** and the **operator** who runs the application against a
PostgreSQL server (local, on a VM, or a managed service).

### User Story 1 - Same code, PostgreSQL database (Priority: P1)

A developer who built an application on SQLite points `database_url` at PostgreSQL and everything
works unchanged: create tables, create/read/update/delete, filters, ordering, pagination, bulk
operations.

```python
app = App(database_url="postgresql://shop:secret@localhost:5432/shop")
```

**Why this priority**: PostgreSQL is the production database of the MVP (vision §4); without it
Ferro cannot run real business applications.

**Independent Test**: Run the complete ORM test suite of Feature 002 against a PostgreSQL database
and get the same results as on SQLite.

**Acceptance Scenarios**:

1. **Given** a `postgresql://` (or `postgres://`) URL, **When** the application starts or
   `ferro.db.connect(url)` runs, **Then** the ORM uses PostgreSQL.
2. **Given** models from Feature 002, **When** `ferro db create-tables` runs against PostgreSQL,
   **Then** tables, unique constraints and indexes are created with native PostgreSQL types.
3. **Given** the Feature 002 ORM test suite, **When** it runs against PostgreSQL, **Then** every test
   passes with identical observable behavior, except the documented improvements below.
4. **Given** a schema-qualified setup (`?options=-csearch_path=tenant1` or the server's default
   schema), **Then** tables are created and queried in that schema.

---

### User Story 2 - Exact and native values (Priority: P1)

Values keep their full precision and native types in PostgreSQL.

**Why this priority**: Money and timestamps must be exact in business applications; this is the
main reason to run PostgreSQL in production.

**Independent Test**: Store edge values for every field type (including a 38-digit Decimal) and
read them back; inspect column types in the database catalog.

**Acceptance Scenarios**:

1. **Given** `Decimal(max_digits=38, decimal_places=10)`, **When** storing
   `Decimal("1234567890123456789012345678.0123456789")`, **Then** it reads back exactly equal.
2. **Given** each field type, **Then** the column type is native: `varchar(n)`, `text`, `integer`,
   `bigint`, `double precision`, `numeric(d,p)`, `boolean`, `date`, `timestamp with time zone`,
   `time`, `uuid`, `jsonb`; the automatic key is an identity `bigint`.
3. **Given** `icontains`/`iexact` with non-ASCII text (e.g. `"ÉCOLE"` vs `"école"`), **Then** the
   match is case-insensitive for all Unicode letters on PostgreSQL.
4. **Given** comparisons, ordering and `in` on decimals, dates, datetimes, UUIDs, **Then** they behave
   by value, not as text.

---

### User Story 3 - Production-ready connections (Priority: P2)

The operator connects securely and the application stays healthy under concurrency and transient
failures.

**Why this priority**: Production databases are remote, often managed, require encrypted
connections, and occasionally restart.

**Independent Test**: Run concurrent requests through a pool; restart the database server; connect
with and without TLS; check that credentials never appear in errors or logs.

**Acceptance Scenarios**:

1. **Given** `pool_size` (URL `?pool_size=N`, `db.connect(url, pool_size=N)`), **Then** at most N
   connections are opened and concurrent queries share them; default is 10.
2. **Given** `sslmode=require` (or `verify-full`), **Then** the connection is encrypted and the server
   certificate is verified; a server without TLS is refused. `sslmode=disable` connects without
   TLS; `prefer` tries TLS first.
3. **Given** a custom CA (`sslrootcert=/path/ca.pem`), **Then** certificates signed by it are trusted.
4. **Given** the database server restarts, **When** the next query runs, **Then** the pool
   reconnects and the query succeeds (or fails once with a clear error if the server is still down);
   the application does not need restarting.
5. **Given** any connection error, **Then** the message names host, port and database but never the
   password.

---

### User Story 4 - Clear PostgreSQL errors (Priority: P2)

Database errors from PostgreSQL map to the same `ferro.models` exceptions as SQLite.

**Why this priority**: Application code must not depend on which database is configured.

**Independent Test**: Trigger unique, not-null, missing-table and connection errors on both
databases and compare exception types and the model/field information.

**Acceptance Scenarios**:

1. **Given** a unique violation, **Then** `IntegrityError` names the model and field(s), as on SQLite.
2. **Given** a missing table, **Then** `OperationalError` suggests `ferro db create-tables`.
3. **Given** wrong credentials, unknown database or unreachable host, **Then** `OperationalError` (at
   connect time) explains the problem without the password.
4. **Given** errors and logs, **Then** they never name the driver libraries.

### Edge Cases

- `postgres://` and `postgresql://` are both accepted; unknown query parameters are rejected with
  a clear error rather than ignored.
- A password containing special characters (percent-encoded in the URL) works.
- Queries running when the server restarts fail with `OperationalError`; later queries reconnect.
- Empty `__in=[]`, `exclude` with NULLs, `LIKE` wildcards in values: same results as SQLite.
- Integers beyond the column range are rejected before reaching the database (as on SQLite).
- `DateTime` values are stored with microsecond precision and returned as UTC.
- Tables in other schemas with the same name do not confuse `create-tables`.
- Pool exhausted: queries wait for a free connection (bounded queue); they do not fail.

## Requirements *(mandatory)*

### Functional Requirements

**Connection**

- **FR-001**: `database_url` MUST accept `postgresql://[user[:password]@]host[:port]/database[?params]`
  and `postgres://...`, through every existing configuration path (App, environment, CLI,
  `ferro.db.connect`).
- **FR-002**: Supported URL parameters MUST include `sslmode` (`disable`, `prefer`, `require`,
  `verify-full`), `sslrootcert`, `pool_size`, `connect_timeout` and `options`; unknown parameters
  MUST raise `ConfigurationError` listing the supported ones.
- **FR-003**: Connections MUST be pooled (default 10, configurable) and opened eagerly enough that a
  bad URL, wrong credentials or an unreachable server fail at connect time.
- **FR-004**: TLS modes `require`/`verify-full` MUST verify the server certificate (public roots, or
  `sslrootcert`); `prefer` MUST fall back to plain TCP only if the server does not offer TLS.
- **FR-005**: A broken connection MUST be replaced automatically on the next query.
- **FR-006**: No error message or log line may contain the password.

**Schema and values**

- **FR-007**: `create-tables` MUST create PostgreSQL-native column types per US2 scenario 2, the
  automatic key as `bigint GENERATED BY DEFAULT AS IDENTITY`, unique constraints and indexes, in
  the connection's current schema, and list existing tables from that schema only.
- **FR-008**: Decimal values MUST be stored and read back exactly (up to `max_digits` 38) on
  PostgreSQL.
- **FR-009**: Date, DateTime, Time, UUID and JSON values MUST be stored using native types and
  round-trip with the same Python types as on SQLite.
- **FR-010**: Case-insensitive lookups (`iexact`, `icontains`) MUST be Unicode-aware on PostgreSQL;
  case-sensitive lookups (`contains`, `startswith`, `endswith`) MUST treat `%`, `_` and `\` in
  values literally.

**Behavior parity**

- **FR-011**: Every QuerySet operation, instance method and bulk operation from Feature 002 MUST
  work on PostgreSQL with the same results, return values and exception types.
- **FR-012**: `IntegrityError` MUST name model and field(s) for unique and not-null violations;
  missing tables MUST raise `OperationalError` with the create-tables hint.
- **FR-013**: The automated ORM test suite MUST run against SQLite and PostgreSQL; PostgreSQL tests
  MUST run automatically when a local PostgreSQL installation or `FERRO_TEST_POSTGRES_URL` is
  available.

**Architecture**

- **FR-014**: The PostgreSQL driver MUST stay behind the backend adapter; the ORM core and Python
  layer MUST contain no PostgreSQL-specific code except the SQL dialect.
- **FR-015**: Query execution MUST NOT block the Python execution thread or HTTP workers.

### Key Entities

- **PostgreSQL configuration**: host, port, database, user, password (secret), TLS mode, root
  certificate, pool size, connect timeout, session options.
- **Dialect**: SQL spelling per database (placeholders, types, text lookups, paging, identity key).
- **Connection pool**: up to N live connections, each running one job at a time, replaced when
  broken.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of the Feature 002 ORM test suite passes on PostgreSQL, run automatically next to
  SQLite.
- **SC-002**: A Decimal with 38 significant digits round-trips exactly; every field edge value from
  Feature 002 SC-006 round-trips on PostgreSQL.
- **SC-003**: With 100 concurrent clients on a PostgreSQL-backed list endpoint for 15 s, zero
  requests fail and at most `pool_size` server connections are used.
- **SC-004**: After a PostgreSQL restart, the first query that runs once the server is back succeeds
  without restarting the application.
- **SC-005**: 0 occurrences of the password in any error message or log line across the error test
  cases.

## Assumptions

- PostgreSQL 13 or newer.
- One database per process, as in Feature 002.
- Migrations (altering existing tables) are Feature 004; relations and transactions Feature 005.
- Tests start a temporary local PostgreSQL cluster (trust authentication, random port, deleted after
  the run) when PostgreSQL server binaries are installed; otherwise they use
  `FERRO_TEST_POSTGRES_URL`; otherwise PostgreSQL tests are skipped with a visible reason. CI
  provides a PostgreSQL service.
- `sslmode=verify-ca` and client certificates are out of scope for this feature.
