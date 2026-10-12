# Contract: Health and readiness probes

Answered by the engine before routing; no application code, middleware, rate limit or CSRF
check runs. Every response carries the usual security headers, `x-request-id`,
`Content-Type: application/json` and `Cache-Control: no-store`.

## Liveness — `GET|HEAD {health_path}` (default `/healthz`)

| Condition | Status | Body |
|---|---|---|
| the engine answers | 200 | `{"status": "ok"}` |

## Readiness — `GET|HEAD {ready_path}` (default `/readyz`)

| Condition | Status | Body |
|---|---|---|
| serving, database healthy or not configured | 200 | `{"status": "ready"}` |
| draining or stopping | 503 | `{"status": "not ready", "failing": ["server"]}` |
| database check failed (error or > 750 ms) | 503 | `{"status": "not ready", "failing": ["database"]}` |
| both | 503 | `{"status": "not ready", "failing": ["server", "database"]}` |

503 responses also carry `Retry-After: 1`. No other text (versions, hosts, database URL or
error messages) ever appears.

## Other methods

`POST`, `PUT`, … on a probe address → 405 error envelope with `Allow: GET, HEAD`.

## Configuration

- `health_path` / `ready_path`: absolute paths; `""` disables that probe (the address then
  behaves like any other path).
- A route registered on a probe or metrics address (same path, trailing slash ignored) stops
  startup with `ConfigurationError: route '/healthz' (handler app.health) uses the liveness
  probe's address; move the probe with health_path or disable it with health_path=""`.

## Logging

Probe requests are logged on `ferro.access` at debug level only.
