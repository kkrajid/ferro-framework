# Feature Specification: Migration Engine

**Feature Branch**: `004-migration-engine`

**Created**: 2026-10-10

**Status**: Draft

**Input**: User description: "Feature 004 from docs/roadmap.md: Django-like migrations —
`ferro makemigrations`, `ferro migrate`, `ferro rollback`, `ferro migration status`; schema
snapshot and diff, migration history table, reversible migrations, transactional where the
database supports it; works on SQLite and PostgreSQL (vision §9, Phase 5). The user explicitly
asked for Django's `makemigrations`/`migrate` simplicity."

## Clarifications

### Session 2026-10-10

The user asked to continue without interruptions; recommended options were taken and recorded.

- Q: What format do migration files use? → A: Python files in `migrations/` (`0001_initial.py`,
  `0002_add_product_stock.py`), each defining `migration = Migration(dependencies=[...],
  operations=[...])` with declarative operations — readable, reviewable and editable like Django's.
- Q: How is the previous schema known? → A: By replaying all migration files in order (no separate
  snapshot file), as Django does; `makemigrations` needs no database connection.
- Q: What happens when a column looks renamed (one removed, one added with the same type)? → A:
  `makemigrations` generates remove + add and prints a warning explaining how to change it to
  `RenameColumn`; it never guesses silently.
- Q: Adding a non-null column to an existing table? → A: The field's static `default` back-fills
  existing rows; without one, `makemigrations` stops with an error suggesting `null=True` or a
  default (no interactive prompts, so it works in CI).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Create and apply the first migration (Priority: P1)

```bash
ferro makemigrations      # Created migrations/0001_initial.py
ferro migrate             # Applying 0001_initial... OK
```

**Why this priority**: This is the Django workflow the user asked for; it replaces
`create-tables` as the way to build the schema.

**Independent Test**: In a project with models and no migrations, run both commands against an
empty database and verify the tables, the migration file and the history record.

**Acceptance Scenarios**:

1. **Given** models and no `migrations/` directory, **When** running `ferro makemigrations`,
   **Then** `migrations/0001_initial.py` is created with one `CreateTable` per model (plus indexes),
   and the command prints what it created.
2. **Given** the generated file, **When** running `ferro migrate`, **Then** the tables exist with the
   same definition `create-tables` would produce, and `0001_initial` is recorded in the history
   table `ferro_migrations`.
3. **Given** nothing changed, **When** running `ferro makemigrations` again, **Then** it prints
   "No changes detected" and creates no file; `ferro migrate` prints "No migrations to apply".
4. **Given** `makemigrations` runs, **Then** it does not need a database connection.

---

### User Story 2 - Evolve models (Priority: P1)

A developer adds, removes and changes fields and models; `makemigrations` writes a migration with
exactly those changes and `migrate` applies them without losing other data.

**Why this priority**: Schemas change constantly; this is the core value of migrations.

**Independent Test**: Apply 0001, insert rows, change models in several ways, generate and apply
0002, and verify both schema and existing data.

**Acceptance Scenarios**:

1. **Given** a new model, **Then** the migration contains `CreateTable`.
2. **Given** a new nullable field, **Then** `AddColumn`; existing rows get NULL.
3. **Given** a new non-null field with a static default, **Then** `AddColumn(..., default=...)` and
   existing rows get the default; without a default `makemigrations` fails with an explanation.
4. **Given** a removed field, **Then** `RemoveColumn`; given a removed model, `DropTable` (with a
   warning printed).
5. **Given** a changed field (type, `max_length`, `null`, `unique`), **Then** `AlterColumn`;
   existing data is kept and converted where the database can convert it.
6. **Given** `index=True` added or removed, **Then** `CreateIndex` / `DropIndex`.
7. **Given** a field renamed in the model, **Then** remove + add are generated and a warning shows
   how to use `RenameColumn` instead; a hand-edited `RenameColumn` migration keeps the data.
8. **Given** `--name custom`, **Then** the file is `000N_custom.py`; given `--check`, the command
   exits 1 when changes are pending and writes nothing (for CI); `--empty` creates an empty
   migration to edit by hand.

---

### User Story 3 - Roll back and inspect (Priority: P2)

```bash
ferro migration status    # [X] 0001_initial  [X] 0002_add_stock  [ ] 0003_alter_price
ferro rollback            # Reverting 0002_add_stock... OK
ferro rollback --to 0001_initial
```

**Why this priority**: Mistakes happen in development and deployments; being able to step back
safely is expected.

**Independent Test**: Apply three migrations, roll back one and then to a target, and compare the
schema with the earlier state; check `status` after each step.

**Acceptance Scenarios**:

1. **Given** applied migrations, **When** `ferro rollback`, **Then** the last migration is reverted
   (operations reversed in reverse order) and removed from history; `--steps N` reverts N;
   `--to NAME` reverts every migration after NAME; `--to zero` reverts all.
2. **Given** a migration that cannot be reversed (it removed a non-null column without a default),
   **Then** `rollback` refuses before changing anything and explains why.
3. **Given** `ferro migration status`, **Then** every migration file is listed in order with
   applied/unapplied markers, and history entries without a file are reported as errors.

---

### User Story 4 - Safe and atomic on both databases (Priority: P2)

**Why this priority**: A half-applied migration corrupts the schema.

**Independent Test**: Make a migration fail halfway (e.g. a unique constraint on duplicate data) on
SQLite and PostgreSQL and verify nothing from it remains.

**Acceptance Scenarios**:

1. **Given** a migration whose second operation fails, **Then** none of its operations remain and it
   is not recorded as applied; earlier migrations stay applied.
2. **Given** SQLite, **Then** changes SQLite cannot do in place (alter column, drop a constrained
   column, add a non-null column) are done by rebuilding the table inside the same transaction,
   keeping rows and indexes.
3. **Given** a project that has a `migrations/` directory, **When** running `ferro db create-tables`,
   **Then** it refuses and points to `ferro migrate`; **When** the server starts with unapplied
   migrations, **Then** it logs a warning naming them.
4. **Given** the migration files of a project, **Then** the SQL they run is generated from the
   operations with identifiers quoted and values (defaults) bound as parameters.

### Edge Cases

- Migration files edited so that history and files disagree (renamed/deleted applied file): error
  naming the migration; nothing is applied.
- Non-linear history (two `0003_*` files, wrong `dependencies`): error explaining which files
  conflict.
- Changing a primary key or an automatic `id`: `makemigrations` stops with an explanation (not
  supported in this feature).
- `AlterColumn` making a column NOT NULL while NULLs exist: the database error is reported and the
  migration is rolled back.
- Model `Meta.table` renamed: drop + create with a warning (use `RenameTable` by hand to keep data).
- Running `migrate` concurrently from two processes: the second waits or fails cleanly; history
  is never duplicated (unique name).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `ferro makemigrations [TARGET] [--name NAME] [--check] [--empty] [--migrations-dir DIR]`
  MUST compare the models with the schema obtained by replaying existing migration files and write
  the next numbered migration file containing the needed operations.
- **FR-002**: Supported operations: `CreateTable`, `DropTable`, `RenameTable`, `AddColumn`,
  `RemoveColumn`, `AlterColumn`, `RenameColumn`, `CreateIndex`, `DropIndex`.
- **FR-003**: Migration files MUST be valid Python that a developer can read and edit; each declares
  its dependency on the previous migration; history MUST be linear.
- **FR-004**: `ferro migrate [TARGET] [--database-url URL]` MUST apply unapplied migrations in order,
  each in its own transaction together with its history record.
- **FR-005**: `ferro rollback [TARGET] [--steps N | --to NAME|zero]` MUST revert applied migrations
  in reverse order, each atomically, computing reverse operations from the replayed schema.
- **FR-006**: `ferro migration status [TARGET]` MUST list migrations with applied state.
- **FR-007**: The history table `ferro_migrations` MUST be created automatically and record each
  applied migration name and time.
- **FR-008**: Non-null columns added to existing tables MUST be back-filled from a static default;
  callable defaults or missing defaults MUST produce a clear `makemigrations` error.
- **FR-009**: On SQLite, operations not supported by `ALTER TABLE` MUST be executed by table
  rebuild preserving data, indexes and automatic key sequences.
- **FR-010**: The generated schema MUST equal what `create-tables` produces for the same models on
  both databases (one DDL source).
- **FR-011**: `create-tables` MUST refuse in projects with a migrations directory; server start MUST
  warn about unapplied migrations.
- **FR-012**: Reverse of a migration that dropped data without a way to restore the column MUST be
  rejected before any change.
- **FR-013**: All SQL MUST be generated in the core from operations; default values MUST be bound
  parameters.
- **FR-014**: After a migration, already-running connections MUST keep working (cached statements
  are refreshed).

### Key Entities

- **Migration**: name (`0003_alter_product_price`), dependency, ordered operations.
- **Operation**: one schema change with everything needed to apply and reverse it given the
  schema before it.
- **Schema state**: tables → columns (type, null, unique, primary key, auto) and indexes, built by
  replaying operations.
- **History**: applied migration names in order (`ferro_migrations`).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The Django-style workflow (model change → `makemigrations` → `migrate`) needs exactly
  two commands and no hand-written SQL for every change listed in US2.
- **SC-002**: Every operation applies and rolls back on SQLite and PostgreSQL with existing rows
  preserved (verified by automated tests on both databases).
- **SC-003**: A failing migration leaves zero partial changes in 100% of the failure tests on both
  databases.
- **SC-004**: `makemigrations` immediately after `migrate` reports no changes for every model shape
  in the test suite (diff/replay round trip is exact).

## Assumptions

- One migrations directory per project (`./migrations` by default); multi-app layouts come with
  the project generator (Feature 009).
- Foreign keys and their operations arrive with relations (Feature 005).
- Data migrations (running Python or SQL inside a migration) and squashing are out of scope.
