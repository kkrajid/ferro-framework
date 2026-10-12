# Contract: New settings

Precedence and validation as for every setting: command line > `FERRO_<NAME>` > `App(...)` >
default; an invalid value stops startup naming the setting and its source.

| Setting | Option | Environment | Default | Valid values |
|---|---|---|---|---|
| health_path | — | `FERRO_HEALTH_PATH` | `/healthz` | `""` or a path starting with `/`, no whitespace, no `{` |
| ready_path | — | `FERRO_READY_PATH` | `/readyz` | same |
| metrics | — | `FERRO_METRICS` | `false` | boolean |
| metrics_path | — | `FERRO_METRICS_PATH` | `/metrics` | path as above (not empty) |
| metrics_allow | — | `FERRO_METRICS_ALLOW` (comma-separated) | `127.0.0.1,::1` | IP addresses / CIDR ranges |
| log_format | `--log-format` | `FERRO_LOG_FORMAT` | `text` | `text`, `json` |
| shutdown_drain | `--shutdown-drain` | `FERRO_SHUTDOWN_DRAIN` | `0` | 0–3600 seconds |

- `health_path`, `ready_path` and `metrics_path` (when metrics are on) must differ.
- `shutdown_drain` adds to the time `ferro run --processes N` waits for workers before killing
  them (`shutdown_timeout + shutdown_drain + 5 s`).

## Database URL parameter

| Parameter | Backends | Default | Meaning |
|---|---|---|---|
| `queue_size` | SQLite, PostgreSQL | 1024 | queries waiting for a connection; beyond it new queries fail at once with `DatabaseUnavailableError` |

`sqlite:///app.db?queue_size=64`, `postgresql://user@host/db?pool_size=20&queue_size=200`.
Unknown SQLite URL parameters are a configuration error.

## Changed behaviour (existing settings)

- `request_timeout` now also bounds receiving the request body (one deadline from the end of the
  headers). Body not received in time → 408 and the connection is closed; handler not finished
  in time → 503 + `Retry-After: 1` (unchanged).
- A client that closes its side of the connection while its request runs is treated as gone:
  the handler is cancelled (and an open transaction rolled back). Clients that half-close a
  socket and still wait for the answer are not supported.
