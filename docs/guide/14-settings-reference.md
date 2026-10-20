# Settings reference

Every setting can be given four ways. The first one found wins:

1. a command-line option (`ferro run --port 9000`), where one exists;
2. an environment variable `FERRO_<NAME>` (also from `.env` in the working directory —
   variables already set in the environment win over `.env`);
3. an `App(...)` keyword argument in your code;
4. the default.

Invalid values stop the command with exit code 2 and a message naming the setting and where the
value came from:

```text
error: invalid value 'abc' for setting 'port' from env (FERRO_PORT): expected an integer
```

Booleans accept `1/0`, `true/false`, `yes/no`, `on/off`. Lists (`trusted_proxies`, ...) are
comma-separated in the environment and lists in `App(...)`. Durations are seconds (decimals
allowed).

## Server

| Setting | Option | Default | Range / values | Meaning |
|---|---|---|---|---|
| `host` | `--host` | `127.0.0.1` | IP or hostname | address to listen on; `0.0.0.0` (or `::`) for all interfaces |
| `port` | `--port` | `8000` | 0–65535 | `0` picks a free port |
| `workers` | `--workers` | CPU count | 1–1024 | engine I/O threads per process |
| `processes` | `--processes` | `1` | 1–64 | worker processes (`ferro run` only; `ferro dev` and `App.run()` are single-process) |
| `sync_threads` | `--sync-threads` | `40` | 1–1024 | threads for plain `def` handlers, per process |
| `debug` | `--debug` / `--no-debug` | on for `ferro dev`, off for `ferro run` | bool | tracebacks in error responses, non-`Secure` cookies; never in production |
| `max_body_size` | `--max-body-size` | `1048576` (1 MiB) | 1 B – 1 GiB | larger bodies get 413 |
| `request_timeout` | `--request-timeout` | `30` | 0.1–3600 s | time for receiving the body plus running the handler (408/503) |
| `header_timeout` | `--header-timeout` | `5` | 0.1–300 s | time for a client to send its request headers (and finish a TLS handshake) |
| `shutdown_timeout` | `--shutdown-timeout` | `30` | 0–3600 s | how long requests in flight may finish after a stop signal |
| `shutdown_drain` | `--shutdown-drain` | `0` | 0–3600 s | before stopping, report not-ready on `/readyz` for this long while still serving |
| `max_connections` | `--max-connections` | `10000` | 1–1,000,000 | open client connections |
| `tls_cert` / `tls_key` | `--tls-cert` / `--tls-key` | — | PEM file paths, both or neither | serve HTTPS (TLS 1.2/1.3) and HTTP/2 |

## Logging and monitoring

| Setting | Option | Default | Values |
|---|---|---|---|
| `log_level` | `--log-level` | `info` | `trace`, `debug`, `info`, `warning` (`warn`), `error` |
| `log_format` | `--log-format` | `text` | `text`, `json` |
| `health_path` | — | `/healthz` | a path, or empty to disable |
| `ready_path` | — | `/readyz` | a path, or empty to disable |
| `metrics` | — | off | bool |
| `metrics_path` | — | `/metrics` | a path |
| `metrics_allow` | — | `127.0.0.1, ::1` | IP addresses or CIDR ranges allowed to read metrics |

## Database, cache and broadcast

| Setting | Option | Default | Values |
|---|---|---|---|
| `database_url` | `--database-url` | none | `sqlite:///path.db`, `sqlite:///:memory:`, `postgresql://user:pass@host:5432/db?...` — URL parameters in [Models](04-models-and-queries.md#connecting) |
| `cache` | — | `memory` | `memory`, `database` |
| `broadcast` | — | `memory` | `memory` (one process), `database` (PostgreSQL `LISTEN`/`NOTIFY`, every process and machine; refused with SQLite) — see [Real-time](16-realtime.md#broadcast-one-message-to-many-connections) |

## Security

| Setting | Default | Values | Meaning |
|---|---|---|---|
| `csrf` | on | bool | cross-origin protection for unsafe methods |
| `trusted_origins` | none | origins (`https://app.example.com`) | other sites allowed to send unsafe requests |
| `trusted_proxies` | none | IPs / CIDR ranges | proxies whose `X-Forwarded-For` / `X-Forwarded-Proto` are believed |
| `rate_limit` | none | `N/second`, `N/minute`, `N/hour`, `N/day` | app-wide limit per client |
| `cors` | none | `ferro.CORS(...)` — `App(cors=...)` only | cross-origin browser access |
| `allowed_hosts` | none (any host) | names: `example.com`, `.example.com` (and subdomains), `*` | `Host` headers this site answers; others get 400 |
| `https_redirect` | off | bool | redirect plain-HTTP requests (per `X-Forwarded-Proto` from a trusted proxy) to HTTPS with 308 |
| `hsts` | off | bool | send `Strict-Transport-Security` |
| `hsts_max_age` | `31536000` | 0 – 2 years, seconds | HSTS duration |
| `hsts_include_subdomains` | on | bool | `includeSubDomains` in the HSTS header |
| `hsts_preload` | off | bool | `preload` (needs `hsts_include_subdomains` and at least one year) |
| `content_security_policy` | none | header value | send `Content-Security-Policy` |

## Route options

Per route, on the decorators (`@app.get(path, ...)`):

| Option | Meaning |
|---|---|
| `rate_limit="5/minute"` | this route's own limit |
| `csrf=False` | exempt from the cross-origin check (on `@app.websocket`: the handshake's origin check) |
| `name="..."` | display name |

## Example `.env`

```bash
FERRO_DATABASE_URL=postgresql://shop:secret@db.internal:5432/shop?sslmode=require&pool_size=10
FERRO_HOST=0.0.0.0
FERRO_PORT=8000
FERRO_PROCESSES=4
FERRO_WORKERS=2
FERRO_LOG_FORMAT=json
FERRO_SHUTDOWN_DRAIN=5
FERRO_TRUSTED_PROXIES=10.0.0.0/8
FERRO_RATE_LIMIT=300/minute
FERRO_HSTS=1
FERRO_METRICS=1
FERRO_METRICS_ALLOW=10.0.0.0/8
FERRO_CACHE=database
FERRO_BROADCAST=database
```

## Other environment variables

| Variable | Used by |
|---|---|
| `FERRO_SUPERUSER_PASSWORD` | `ferro createsuperuser --no-input` |
