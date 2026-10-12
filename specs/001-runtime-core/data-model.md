# Data Model: Runtime Core & Python Handlers

No persistent data in this feature. These are the runtime entities, their fields and validation
rules. Rust names are given for `crates/*`; Python names for `python/ferro`.

## Settings (`ferro_core::Settings`, `ferro.config.Settings`)

| Field | Type | Default | Env var | CLI option | Validation |
|---|---|---|---|---|---|
| host | str | `127.0.0.1` | `FERRO_HOST` | `--host` | valid IP or hostname |
| port | int | `8000` | `FERRO_PORT` | `--port` | 0–65535 (0 = ephemeral) |
| workers | int | CPU count | `FERRO_WORKERS` | `--workers` | 1–1024 |
| debug | bool | `true` for `dev`, `false` for `run` | `FERRO_DEBUG` | `--debug/--no-debug` | `1/0/true/false/yes/no/on/off` |
| max_body_size | int (bytes) | 1,048,576 | `FERRO_MAX_BODY_SIZE` | `--max-body-size` | 1 B – 1 GiB |
| request_timeout | float (s) | 30 | `FERRO_REQUEST_TIMEOUT` | `--request-timeout` | 0.1 – 3600 |
| shutdown_timeout | float (s) | 30 | `FERRO_SHUTDOWN_TIMEOUT` | `--shutdown-timeout` | 0 – 3600 |
| log_level | str | `info` | `FERRO_LOG_LEVEL` | `--log-level` | `trace/debug/info/warning/error` |
| max_connections | int | 10,000 | `FERRO_MAX_CONNECTIONS` | `--max-connections` | 1 – 1,000,000 |
| header_timeout | float (s) | 5 | `FERRO_HEADER_TIMEOUT` | `--header-timeout` | 0.1 – 300 |
| sync_threads | int | 40 | `FERRO_SYNC_THREADS` | `--sync-threads` | 1 – 1024 |

Precedence: CLI > environment > `App(...)` keyword > default. Each resolved value records its source
(`cli`, `env`, `app`, `default`) for the startup log. Invalid values raise `ConfigurationError`
naming the setting, the source and the accepted range.

## Route (`ferro.app.RouteRecord` → `ferro_router::RouteSpec`)

| Field | Type | Rules |
|---|---|---|
| methods | set of `GET/POST/PUT/PATCH/DELETE/OPTIONS` | non-empty; `HEAD` not declared explicitly (derived from GET) |
| path | str | starts with `/`; segments static or `{name}`; name = `[A-Za-z_][A-Za-z0-9_]*`; unique names per path; no empty segments except a trailing slash |
| handler | callable | `async def` or `def` |
| endpoint | coroutine function | Python wrapper built by `routing.py`; this is what Rust calls |
| name | str | `module.qualname` of handler (used by `ferro routes`, errors) |

Duplicate `(method, normalized path)` → `ConfigurationError` naming both handlers. Two paths that
differ only in parameter names at the same position (`/a/{x}` vs `/a/{y}`) are duplicates.

## PathPattern (`ferro_router::PathPattern`)

Ordered list of `Segment::Static(String)` / `Segment::Param(String)` plus `trailing_slash: bool`.
Static segments are stored percent-decoded.

## RouteMatch outcome (`ferro_router::Match`)

| Variant | Data | HTTP result |
|---|---|---|
| `Found` | route id, path params (decoded) | handler invoked |
| `MethodNotAllowed` | allowed methods (sorted, includes HEAD if GET) | 405 + `allow` |
| `Redirect` | target path (trailing slash toggled) | 308 + `location` (+ original query) |
| `NotFound` | — | 404 |

Precedence at each segment: static child, then parameter child (with backtracking).

## HandlerSignature (`ferro.routing.ParamSpec`)

One entry per handler parameter: `name`, `source` (`request` / `path` / `query`), `converter`
(`int`, `float`, `bool`, `str`, or none), `required` (no default), `default`. Rejected at
registration: `*args`, `**kwargs`, positional-only parameters, annotations other than
`int/float/bool/str/Optional[...]/Request/None`.

Boolean query conversion: `true/1/yes/on` → True, `false/0/no/off` → False (case-insensitive),
anything else → 422.

## Request (`ferro_core::FerroRequest`, exposed as `ferro.Request` pyclass)

| Attribute | Type | Notes |
|---|---|---|
| method | str | upper-case |
| path | str | decoded path |
| raw_path | str | as received |
| query_string | str | raw |
| query | `QueryParams` | `get(key, default)`, `getlist(key)`, `items()`, `[key]` (last value) |
| path_params | dict[str, str] | decoded |
| headers | `Headers` | case-insensitive, `get`, `getlist`, `items()` |
| client | tuple[str, int] or None | peer address |
| request_id | str | same as `x-request-id` |
| body() | awaitable → bytes | already read (size-limited) |
| json() | awaitable → Any | 400 on bad JSON or non-JSON content type |

## Response (`ferro.Response`, `ferro.JSONResponse` → `ferro_core::FerroResponse`)

| Field | Type | Rules |
|---|---|---|
| content | Any | `None`, `bytes`, `str`, or JSON-serializable value |
| status | int | 100–599, default 200 |
| headers | Mapping[str, str] or list of pairs | names validated as HTTP tokens; values without CR/LF |
| media_type | str or None | inferred when None: bytes → octet-stream, str → text/plain; charset=utf-8, other → application/json |

Return-value conversion (FR-012): `dict/list/tuple` → JSON 200; `str` → text 200; `bytes` →
binary 200; `None` → 204; `Response` → as given.

## ErrorEnvelope (`ferro_core::ErrorEnvelope`)

```json
{"error": {"status": 422, "message": "Validation failed", "request_id": "…",
           "details": [{"param": "id", "location": "path", "problem": "expected int"}]}}
```

`request_id` always present. `details` only for 422. Debug-mode 500 adds `type`, `exception`,
`traceback` (list of strings). Production 500 message is always `Internal Server Error`.

## Server lifecycle (`ferro._ferro.Server`)

```text
created ──start()──▶ running ──stop(graceful)──▶ stopping ──(in-flight done or timeout)──▶ stopped
   │                                                                                        ▲
   └──start() error (port in use, invalid settings) ──▶ ConfigurationError / OSError ───────┘
```
