# Feature Specification: ORM Completion

**Feature Branch**: `013-orm-completion`

**Created**: 2026-10-11

**Status**: Implemented

**Input**: User description: "Complete the ORM gaps listed as current limits: `select_for_update`,
custom `through=` link models for many-to-many, and model inheritance."

## Clarifications

### Session 2026-10-11

The user asked to complete these items without interruptions; recommended options were taken.

- Q: What happens when `select_for_update()` is used outside a transaction? → A: Evaluating it
  raises `TransactionError` naming the fix (wrap it in `async with transaction():`), as row locks
  only last until the transaction ends.
- Q: How does it behave on SQLite? → A: SQLite has no row locks; Ferro's SQLite transactions
  already hold the database's write lock from their start, so the query runs unchanged and the
  guarantee (no other transaction changes these rows before commit) holds. `nowait` and
  `skip_locked` are accepted and have nothing to wait for.
- Q: Which rows are locked when related objects are joined (`select_related`)? → A: Only the
  queried model's rows (PostgreSQL `FOR UPDATE OF`), so nullable joins work; locking joined rows
  is out of scope.
- Q: What does a custom link model need, and how are links added? → A: `ManyToMany(Tag,
  through="ArticleTag")`; the link model has exactly one foreign key to each side (or names them
  with `through_fields=(source, target)`). `add`/`set` accept `through_defaults={...}` for its
  extra fields; adding an existing pair creates no duplicate.
- Q: Which kind of inheritance? → A: Abstract base models (`class Meta: abstract = True`):
  fields, relations, methods and `Meta` options are inherited; abstract models have no table.
  `related_name` may contain `%(class)s`. Inheriting from a concrete model (multi-table or proxy
  inheritance) stays unsupported with an error that suggests an abstract base.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Lock rows for update (Priority: P1)

A developer reads rows they are about to change inside a transaction, and needs other
transactions to wait (or fail fast, or skip them) until the change commits — for stock,
balances, job queues.

**Why this priority**: Read-modify-write without row locks loses updates under concurrency.

**Independent Test**: Two concurrent transactions lock the same row; the second waits for the
first (or fails at once with `nowait`, or skips it with `skip_locked`).

**Acceptance Scenarios**:

1. **Given** a transaction that locked a row with `select_for_update()`, **When** a second
   transaction locks the same row, **Then** it waits until the first commits and then sees the
   committed value.
2. **Given** `select_for_update(nowait=True)` on a locked row, **Then** it fails at once with
   `DatabaseUnavailableError`.
3. **Given** `select_for_update(skip_locked=True)`, **Then** locked rows are left out of the
   result.
4. **Given** `select_for_update()` evaluated outside a transaction, **Then** `TransactionError`.
5. **Given** `select_for_update().select_related(...)`, **Then** the query works, including
   nullable relations.
6. **Given** `count()`, `exists()`, `aggregate()` or `annotate()` on a locking query set, **Then**
   a clear error (locks apply to fetched rows); `values()` fetches rows and may lock them.

---

### User Story 2 - Many-to-many with a custom link model (Priority: P2)

A developer needs data on the link itself (date joined, role, position) and declares their own
link model.

**Why this priority**: Common modelling need; the automatic link table cannot hold it.

**Independent Test**: Declare `Membership` between `Person` and `Group`, add members with a role,
query through the relation in both directions, read the link rows.

**Acceptance Scenarios**:

1. **Given** `members = ManyToMany(Person, through="Membership")`, **Then** no automatic link
   table is created, migrations create `Membership`'s table, and both sides' managers work.
2. **Given** `await group.members.add(person, through_defaults={"role": "admin"})`, **Then** a
   link row with that role exists; adding the same person again creates no second row.
3. **Given** a link model whose extra fields are required and no `through_defaults`, **Then**
   `add` fails with a message naming the fields.
4. **Given** link rows, **Then** `filter(members__name=...)`, `prefetch_related("members")`,
   `remove`, `set` and `clear` behave as with automatic link models.
5. **Given** a link model without a foreign key to one side, or with two to the same side and no
   `through_fields`, **Then** a clear error at class definition.

---

### User Story 3 - Abstract base models (Priority: P2)

A developer puts shared fields and behaviour (timestamps, owner, soft-delete flag) in an abstract
base class and inherits it in many models.

**Why this priority**: Removes repetition across real applications; widely expected from Django.

**Independent Test**: `class Timestamped(Model)` with `Meta.abstract = True` and two subclasses;
both have the fields, tables and migrations; the base has no table.

**Acceptance Scenarios**:

1. **Given** an abstract base with fields, a foreign key (`related_name="%(class)s_set"`) and
   methods, **Then** each concrete subclass has its own copies of the fields, a distinct reverse
   accessor, the methods, and its own table; the base has none and cannot be queried.
2. **Given** `Meta` options on the base (`unique_together`), **Then** subclasses inherit them
   unless they declare their own `Meta`.
3. **Given** abstract bases inheriting from abstract bases, **Then** all fields are collected in
   order (parents first).
4. **Given** a model inheriting from a concrete model, **Then** an error suggesting an abstract
   base.
5. **Given** subclasses, **Then** `makemigrations` creates their tables with the inherited columns.

### Edge Cases

- A subclass redefining an inherited field replaces it (Django-compatible).
- `select_for_update()` with `limit`/slicing works (lock applies to the returned rows).
- Link-model rows deleted with either side (CASCADE on its foreign keys is the developer's choice).

## Requirements *(mandatory)*

- **FR-001**: `QuerySet.select_for_update(nowait=False, skip_locked=False)`; `nowait` and
  `skip_locked` together are an error.
- **FR-002**: On PostgreSQL the fetch query ends with `FOR UPDATE OF <table>` (+ `NOWAIT` /
  `SKIP LOCKED`); on SQLite no clause is added.
- **FR-003**: Evaluating a locking query outside `transaction()` raises `TransactionError`;
  `count`, `exists`, `aggregate` and `annotate` on it raise `FieldError`; `values()` keeps the lock.
- **FR-004**: `ManyToMany(..., through=Model | "Name", through_fields=None)`; validation of the link
  model's foreign keys at definition; no automatic link model when `through` is given.
- **FR-005**: `add(*objs, through_defaults=None)` and `set(objs, through_defaults=None)` create
  link rows with those values and never duplicate an existing pair; `remove`, `clear`, queries and
  prefetching work unchanged.
- **FR-006**: `class Meta: abstract = True` makes a model abstract: not registered, no table,
  `objects` raises a clear error; concrete subclasses get copies of its fields and relations
  (with `%(class)s` replaced in `related_name`) and inherit its `Meta` unless they define one.
- **FR-007**: Inheriting from a concrete model raises `FieldError` suggesting an abstract base.
- **FR-008**: Every behaviour is tested on SQLite and PostgreSQL (constitution VII); the SQL
  compiler change has SQL-text tests.

## Success Criteria *(mandatory)*

- **SC-001**: With 20 concurrent "read stock, check, decrement" transactions using
  `select_for_update()` on a row with 10 items, exactly 10 succeed and the stock ends at 0 on
  PostgreSQL and SQLite (without the lock, PostgreSQL loses updates).
- **SC-002**: A custom link model with extra fields round-trips through add, query, prefetch and
  migrations on both databases.
- **SC-003**: A model hierarchy with two abstract levels migrates and queries on both databases.

## Assumptions

- `of=`, locking joined rows, multi-table and proxy inheritance are out of scope.
