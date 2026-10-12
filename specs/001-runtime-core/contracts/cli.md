# Contract: `ferro` CLI (Feature 001)

```text
ferro dev    [TARGET] [server options] [--reload/--no-reload] [--reload-dir DIR]...
ferro run    [TARGET] [server options]
ferro routes [TARGET]
ferro --version
```

`TARGET` = `module:attribute`, default `app:app`. The current directory is put on `sys.path`
first. `module` may be dotted (`myproject.main:application`).

## Server options (dev and run)

| Option | Setting |
|---|---|
| `--host TEXT` | host |
| `--port INT` | port |
| `--workers INT` | workers |
| `--debug / --no-debug` | debug |
| `--max-body-size INT` | max_body_size |
| `--request-timeout FLOAT` | request_timeout |
| `--shutdown-timeout FLOAT` | shutdown_timeout |
| `--log-level TEXT` | log_level |
| `--max-connections INT` | max_connections |
| `--header-timeout FLOAT` | header_timeout |
| `--sync-threads INT` | sync_threads |

Defaults and environment variables: see [data-model.md](../data-model.md#settings).
`dev` defaults: `debug=true`, reload on. `run` defaults: `debug=false`, no reload.

## Output

- Startup (stderr): `Ferro <version> serving <target> on http://<host>:<port> (<mode>, <n> workers)`.
- `routes` (stdout): aligned table `METHODS  PATH  HANDLER`, sorted by path then method;
  `HEAD` shown with `GET`.
- Errors (stderr): one line `error: <message>` plus, for import errors, the Python traceback of the
  user module only.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | clean shutdown (signal) or `routes`/`--version` success |
| 1 | runtime failure (unexpected server error) |
| 2 | usage error (bad option) or configuration error (invalid setting, route, target) |
| 3 | bind failure (address in use / permission denied) |

## Signals

SIGINT (Ctrl+C), SIGTERM, and SIGBREAK (Windows) → graceful shutdown bounded by
`shutdown_timeout`, exit 0. A second SIGINT during shutdown forces immediate exit (code 1).
