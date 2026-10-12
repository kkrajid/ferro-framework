---
description: "Task list for Feature 009 — CLI & Project Generator"
---

# Tasks: CLI & Project Generator

**Tests**: Required; each phase's tests are written and observed failing first.

## Phase 1: Foundational

- [X] T001 [P] Tests `tests/python/test_dotenv.py` (parsing rules, environment wins, errors with line numbers) and `tests/python/test_testing.py` (TestClient requests, JSON, cookies, database tables)
- [X] T002 `ferro/dotenv.py`, `ferro/testing.py`; `.env` loading in `ferro.cli.main`

## Phase 2: US1 - New project (P1) 🎯

- [X] T003 [US1] Tests `tests/integration/test_project.py`: files, next steps, refusal/--force, invalid names, `ferro new .`, makemigrations/migrate/routes in the new project, the project's own tests pass
- [X] T004 [US1] `ferro/project.py` + `ferro new`

## Phase 3: US2 - Shell (P1)

- [X] T005 [US2] Tests (in `test_project.py`): `ferro shell -c` with await and models, piped input, exception exit code
- [X] T006 [US2] `ferro/shell.py` + `ferro shell`

## Phase 4: US3 - db shell (P2)

- [X] T007 [US3] Tests: fake `sqlite3` client receives the database path; missing client error; in-memory refused
- [X] T008 [US3] `ferro db shell`

## Phase 5: Polish

- [X] T009 [P] Docs (getting-started: start with `ferro new`; README; roadmap)
- [X] T010 Gates + quickstart validation; notes

## Implementation Notes (recorded during /speckit-implement)

- **Test-first observance.** T001, T003, T005 and T007 were written before the code and observed
  failing (`ferro.dotenv`/`ferro.testing` missing, unknown `ferro new`). After implementing, two
  problems surfaced: a directory that already exists in the working directory counted as an
  importable (namespace) module, so `ferro new taken` was refused for the wrong reason — the
  module check now ignores the working directory; and in the full test session
  `TestClient(create_tables=True)` tried to create every model registered by other test modules
  (including deliberately circular ones) — `create_tables` now also accepts a list of models.
  The test projects were renamed from `site` to `portal`, since `site` is a standard-library
  module and correctly refused.
- **Design notes:** templates are `string.Template` texts inside `ferro/project.py` (no package
  data); `TestClient` uses only the standard library so generated projects need nothing but
  pytest; `ferro shell` is an asyncio REPL with top-level `await` (`-c` and piped input run
  once); `ferro db shell` passes PostgreSQL passwords through `PGPASSWORD`, never the command
  line.
- **Results / quickstart:** the integration test runs the quickstart end to end — `ferro new` →
  `makemigrations` → `migrate` (database URL from `.env`) → `routes` → the generated project's
  own `pytest` → `ferro shell -c` and piped input. `createsuperuser` was delivered and validated
  in Feature 007.
- **Gates:** ruff, `pytest` (619 passed, 2 skipped); no Rust changes (`cargo test` 150 from
  Feature 008) — Windows 11, CPython 3.13, PostgreSQL 18.1.
- **Deferred:** multiple apps per project, custom templates.
