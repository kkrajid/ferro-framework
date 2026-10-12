# Command-line reference

```text
ferro [--version] <command> [options]
```

Every command reads `./.env` first (`KEY=VALUE` lines; variables already in the environment
win). Commands that need your application take an optional **target** `module:attribute`
(default `app:app`, i.e. `app` in `app.py` of the current directory).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success / clean shutdown |
| 1 | runtime failure (e.g. a migration failed, `makemigrations --check` found changes, workers keep crashing) |
| 2 | configuration or usage error (invalid setting, unknown target, bad certificate) |
| 3 | the address is already in use |

## Projects

### `ferro new NAME`

Creates a project in `NAME/` (or the current empty directory with `.`): `app.py`, `models.py`,
`admin.py`, `tests/`, `migrations/`, `.env` (owner-only on POSIX), `.env.example`, `.gitignore`,
`pyproject.toml`, `README.md`. `--force` writes into a non-empty directory (existing files are
kept). The name must be a valid Python identifier that does not shadow a module.

## Serving

### `ferro dev [target]`

Development server: debug on (tracebacks in error responses), restarts when a `.py` file
changes, one process.

| Option | |
|---|---|
| `--reload` / `--no-reload` | auto-reload (default on) |
| `--reload-dir DIR` | directory to watch (repeatable; default: the current directory) |
| all server options below | |

### `ferro run [target]`

Production server: debug off, no reload, `Secure` cookies.

| Option | Setting |
|---|---|
| `--host STR`, `--port INT` | address |
| `--processes INT` | worker processes sharing the port |
| `--workers INT` | engine I/O threads per process |
| `--sync-threads INT` | threads for `def` handlers |
| `--max-body-size INT`, `--max-connections INT` | limits |
| `--request-timeout FLOAT`, `--header-timeout FLOAT` | timeouts (seconds) |
| `--shutdown-timeout FLOAT`, `--shutdown-drain FLOAT` | stopping (seconds) |
| `--log-level STR`, `--log-format STR` | logging |
| `--database-url STR` | database |
| `--tls-cert PATH`, `--tls-key PATH` | HTTPS |
| `--debug` / `--no-debug` | override the mode's default |

Details for each in the [settings reference](14-settings-reference.md).

Stopping: `Ctrl+C`, `SIGTERM` (or `CTRL_BREAK` on Windows) stops gracefully — drain, then let
requests finish within `shutdown_timeout`. A second signal stops at once. With `--processes N`,
the supervisor restarts a worker that dies, and exits with code 1 if workers keep failing right
after starting (an import error, say).

### `ferro routes [target]`

Prints the route table (methods, path, handler) without starting a server.

## Database

### `ferro makemigrations [target]`

Writes the next migration for model changes into `migrations/`.

| Option | |
|---|---|
| `--name NAME` | file name instead of an automatic one |
| `--check` | write nothing; exit 1 if changes are pending (CI) |
| `--empty` | an empty migration to edit by hand |
| `--migrations-dir DIR` | default `migrations` |

### `ferro migrate [target]`

Applies unapplied migrations in order, each in one transaction, then creates missing model
permissions. Options: `--database-url URL`, `--migrations-dir DIR`.

### `ferro rollback [target]`

Reverts applied migrations, newest first. `--steps N` (default 1), or `--to NAME` (keep `NAME`
applied; `--to zero` reverts all). Options: `--database-url`, `--migrations-dir`.

### `ferro migration status [target]`

Lists migration files and whether each is applied.

### `ferro db create-tables [target]`

Creates missing tables straight from the models, without migrations (quick experiments only).

### `ferro db shell [target]`

Opens the database's own client (`sqlite3` or `psql`, which must be installed) connected to the
configured database.

## Users and interactive work

### `ferro createsuperuser --username NAME [target]`

Creates an active staff superuser. `--email EMAIL`; `--no-input` takes the password from
`FERRO_SUPERUSER_PASSWORD` instead of asking; `--database-url URL`.

### `ferro shell [target]`

A Python shell with your app and models imported, the database connected and top-level `await`:

```text
>>> await Product.objects.filter(stock=0).count()
3
```

`-c CODE` runs code and exits (`ferro shell -c "print(await User.objects.count())"`);
`--database-url URL`.
