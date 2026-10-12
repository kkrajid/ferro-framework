# Feature Specification: CLI & Project Generator

**Feature Branch**: `009-cli-generator`

**Created**: 2026-10-11

**Status**: Draft

**Input**: User description: "Feature 009 from docs/roadmap.md (vision Phase 8, §5, §21): `ferro
new`, `shell`, `db`, `createsuperuser`, templates — so that `ferro new ecommerce && ferro dev`
works like Django's startproject."

## Clarifications

### Session 2026-10-11

The user asked to continue without interruptions; recommended options were taken and recorded.

- Q: What does `ferro new` generate? → A: A small, runnable project: `app.py` (the `App`, routes,
  auth and admin wiring), `models.py`, `admin.py`, `tests/test_app.py`, `migrations/` (empty),
  `.env` (SQLite database URL), `.env.example`, `.gitignore`, `pyproject.toml` and `README.md`.
  One template; no interactive questions.
- Q: Where do settings come from? → A: The existing precedence (CLI > environment > `App(...)`)
  plus a `.env` file in the working directory, read by every `ferro` command; variables already
  set in the environment win over `.env`.
- Q: How does `ferro shell` handle async ORM calls? → A: It is an asyncio REPL: top-level
  `await` works, the app's models are preloaded and the database is connected. With `-c CODE`
  or piped input it runs non-interactively (scripts, CI).
- Q: What is `ferro db`? → A: `ferro db shell` opens the database's own client (`sqlite3` or
  `psql`) with the configured URL (password passed through the environment, never the command
  line); `ferro db create-tables` stays.
- Q: `createsuperuser`? → A: Already delivered in Feature 007; the generated README uses it.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start a project (Priority: P1)

```bash
ferro new shop
cd shop
ferro makemigrations && ferro migrate
ferro dev
```

**Why this priority**: The MVP Definition of Done starts with `ferro new`.

**Acceptance Scenarios**:

1. **Given** `ferro new shop`, **Then** the directory contains the files above, prints next steps,
   and refuses to overwrite an existing non-empty directory (exit 2) unless `--force`.
2. **Given** the new project, **Then** `ferro makemigrations`, `ferro migrate`, `ferro routes` and
   `ferro dev` work without editing anything; `pytest` inside it passes.
3. **Given** an invalid name (`my-shop!`, a Python keyword, an existing module like `json`),
   **Then** a clear error and nothing is created.

---

### User Story 2 - Interactive shell (Priority: P1)

```bash
ferro shell
>>> await Product.objects.count()
3
```

**Acceptance Scenarios**:

1. **Given** `ferro shell`, **Then** a REPL starts with `app`, `db`, `models` and every model class
   of the app in scope, the database connected, and top-level `await` supported.
2. **Given** `ferro shell -c "print(await Product.objects.count())"` or piped input, **Then** the
   code runs and the command exits with 0 (1 on an exception, printed as a traceback).

---

### User Story 3 - `.env` and `ferro db shell` (Priority: P2)

**Acceptance Scenarios**:

1. **Given** `.env` with `FERRO_DATABASE_URL=...`, **Then** every command uses it; an environment
   variable with the same name wins; malformed lines are reported with their line number.
2. **Given** `ferro db shell`, **Then** `sqlite3`/`psql` starts connected to the configured
   database; if the client is not installed, a clear error names it.

### Edge Cases

- `ferro new .` in an empty directory generates into it, deriving the name from the directory.
- `.env` values with quotes, `export ` prefixes, comments and blank lines are handled.
- `ferro shell` without a database configured still starts (ORM calls then explain how to
  configure one).

## Requirements *(mandatory)*

- **FR-001**: `ferro new NAME [--force]` generates the project described above.
- **FR-002**: The generated project includes a model, routes using it, admin registration,
  authentication, and a test using the in-process test client.
- **FR-003**: `ferro shell [TARGET] [-c CODE]` with top-level await, models in scope, database
  connected.
- **FR-004**: `.env` loading for every command (environment wins).
- **FR-005**: `ferro db shell [TARGET]`.
- **FR-006**: A test client for applications (`ferro.testing.TestClient`) used by generated
  tests: starts the app in-process, gives an HTTP client, connects a test database.

## Success Criteria *(mandatory)*

- **SC-001**: From `ferro new` to a running server with a migrated database takes 4 commands and
  no edits.
- **SC-002**: The generated project's own tests pass.
- **SC-003**: Every scenario is covered by automated tests (CLI subprocess tests).

## Assumptions

- Multiple apps per project, custom templates and code formatters for generated files are out of
  scope.
