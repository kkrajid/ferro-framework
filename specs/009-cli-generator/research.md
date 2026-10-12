# Research: CLI & Project Generator

**Feature**: [spec.md](spec.md) | **Date**: 2026-10-11

## R1. Templates

- **Decision**: templates live in `ferro/project.py` as `string.Template` texts (`$name`), so no
  package-data configuration is needed and `{}` in Python code needs no escaping. Generated
  files: `app.py`, `models.py`, `admin.py`, `tests/test_app.py`, `migrations/.gitkeep`, `.env`,
  `.env.example`, `.gitignore`, `pyproject.toml` (with `[tool.pytest.ini_options] pythonpath =
  ["."]`), `README.md`.
- Name rules: a Python identifier, not a keyword, not an importable module (`json`, `ferro`).
  `ferro new .` uses the current (empty) directory's name.

## R2. `.env`

`ferro.dotenv.load(path)` parses `KEY=VALUE` lines (optional `export `, `#` comments, blank
lines, single/double quotes, `\n` escapes in double quotes) and sets variables that are not
already in the environment. Malformed lines raise `ConfigurationError` with the line number.
`ferro.cli.main` loads `./.env` before anything else.

## R3. Shell

An asyncio REPL in the main thread: `code.InteractiveConsole` whose compiler has
`ast.PyCF_ALLOW_TOP_LEVEL_AWAIT`; code objects with coroutine results run with
`loop.run_until_complete`, so ORM calls see a running loop. `-c CODE` or piped stdin runs once
(same compiler flag) and exits 1 on an exception. Namespace: `app`, `db`, `models`, every
registered (non-link) model class by name; the database connects when a URL is configured.

## R4. `ferro db shell`

SQLite URL → `sqlite3 PATH` (`:memory:` refused); PostgreSQL URL → `psql URL-without-password`
with `PGPASSWORD` in the child environment. The client is found with `shutil.which`; missing
clients produce a configuration error naming them.

## R5. Test client

`ferro.testing.TestClient(app, database_url=None, create_tables=True)`: context manager that
starts the app in-process (`ServerRunner`, port 0, one worker, development mode), connects the
database (creating tables for registered models when asked) and offers `get/post/put/patch/
delete/options(path, json=, data=, headers=)` over `http.client` with a cookie jar, returning a
small response (`status_code`, `headers`, `content`, `text`, `json()`). Standard library only,
so generated projects need no test dependencies besides pytest.
