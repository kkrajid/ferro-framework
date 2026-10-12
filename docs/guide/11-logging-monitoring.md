# Logging, health checks and metrics

## Logs

Ferro writes logs to **stderr** — what systemd, Docker, Kubernetes and every platform collect.

```text
2026-10-12T09:30:01.123Z  INFO ferro.access: method=GET path=/notes status=200 duration_ms=3.21 request_id=4f1c… client=203.0.113.9
```

| Setting | Values |
|---|---|
| `--log-level` / `FERRO_LOG_LEVEL` | `trace`, `debug`, `info` (default), `warning`, `error` |
| `--log-format` / `FERRO_LOG_FORMAT` | `text` (default) or `json` (one JSON object per line: `timestamp`, `level`, `target`, `message` and fields) |

Loggers you will see: `ferro.access` (one line per request; probes and metrics only at
`debug`), `ferro.server` (start, stop, warnings), `ferro.python` (your application's logs) and
errors with tracebacks under the request id.

### Your own logs

Use the standard `logging` module:

```python
import logging

log = logging.getLogger("shop")


@app.post("/orders")
async def create_order(request):
    log.info("creating order for %s", request.user.username)
```

Records are forwarded to Ferro's output with the request id of the request being handled — in
`async` and plain `def` handlers alike:

```text
INFO ferro.python: creating order for ann logger=shop request_id=4f1c…
```

If your application configures logging itself (`logging.basicConfig(...)`, handlers on the root
logger), Ferro leaves it alone; `%(request_id)s` is available in your format strings.

### Request ids

Every response carries `x-request-id`. Ferro accepts a well-formed id from the client (a proxy
or the calling service) or generates one, uses it in the access log, in your log records, in
error responses (`"request_id"`) and for the traceback of a `500`. To trace a user's error report,
search the logs for that id.

### Shutdown summary

The last line of a stop says what happened to requests in flight:

```text
stopped: 12 requests in flight at stop, 12 finished, 0 cut off after shutdown_timeout
```

## Health checks

Answered by the engine, without running your code:

| Probe | Response | Use |
|---|---|---|
| `GET /healthz` | `200 {"status": "ok"}` while the process runs | liveness: restart the process if it fails |
| `GET /readyz` | `200 {"status": "ready"}`, or `503 {"status": "not ready", "failing": ["database"]}` / `["server"]` | readiness: send traffic only when it passes |

- The database check is `SELECT 1` with a 750 ms limit, cached for a second however many probes
  arrive.
- During a drain (`--shutdown-drain N`) and shutdown, `/readyz` reports `server` so load
  balancers stop sending traffic before the server stops.
- Change the paths with `FERRO_HEALTH_PATH` / `FERRO_READY_PATH`; an empty value disables one.
  A route on a probe path is a startup error.
- Point liveness at `/healthz`, not `/readyz`: a database outage should take the app out of
  rotation, not restart it in a loop.

## Metrics

Prometheus text format, off by default:

```bash
FERRO_METRICS=1                         # serve /metrics
FERRO_METRICS_ALLOW=10.0.0.0/8          # who may read it (default: 127.0.0.1, ::1); others get 404
FERRO_METRICS_PATH=/metrics
```

| Metric | Type | Labels |
|---|---|---|
| `ferro_http_requests_total` | counter | `route` (the pattern, e.g. `/notes/{id}`), `method`, `status` (`2xx`, `4xx`, …) |
| `ferro_http_request_duration_seconds` | histogram | `route`, `method` |
| `ferro_http_requests_in_flight` | gauge | |
| `ferro_db_queries_total` | counter | `outcome` (`ok`, `error`, `unavailable`) |
| `ferro_db_query_duration_seconds` | histogram | |
| `ferro_process_start_time_seconds` | gauge | |
| `ferro_build_info` | gauge | `version` |

Labels are bounded by your route table (unmatched paths share `<unmatched>`), so a scanner
cannot blow up the number of series. With `--processes N`, any worker answers with totals across
all workers (other workers' numbers at most a second old).

Prometheus:

```yaml
scrape_configs:
  - job_name: shop
    metrics_path: /metrics
    static_configs:
      - targets: ["10.0.1.5:8000", "10.0.1.6:8000"]
```

### What to alert on

| Alert | Expression (PromQL) |
|---|---|
| error rate above 1% | `sum(rate(ferro_http_requests_total{status="5xx"}[5m])) / sum(rate(ferro_http_requests_total[5m])) > 0.01` |
| slow requests | `histogram_quantile(0.99, sum by (le) (rate(ferro_http_request_duration_seconds_bucket[5m]))) > 1` |
| database trouble | `rate(ferro_db_queries_total{outcome="unavailable"}[5m]) > 0` |
| saturation | `ferro_http_requests_in_flight` close to your capacity |
| not ready | your load balancer's health-check status for `/readyz` |

Turning metrics on had no measurable cost in the benchmark (server CPU per request 121.9 µs
on vs 120.9 µs off, within run-to-run noise).
