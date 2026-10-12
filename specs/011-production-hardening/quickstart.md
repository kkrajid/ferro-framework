# Quickstart & Validation: Production Hardening

Prerequisites: a built extension (`maturin develop --uv`), PostgreSQL binaries for the database
scenarios (the test suite starts its own temporary cluster), `oha` for the measurements.

## Probes and drain

```bash
ferro run --shutdown-drain 5            # app with database_url configured
curl -i http://127.0.0.1:8000/healthz   # 200 {"status": "ok"}
curl -i http://127.0.0.1:8000/readyz    # 200 {"status": "ready"}
# stop the database → /readyz answers 503 {"status": "not ready", "failing": ["database"]} within 2 s
# start it again    → /readyz answers 200 within 2 s, no restart
# Ctrl+C            → /readyz answers 503 ("server") for 5 s while requests are still served,
#                     then the server stops and logs "stopped: N requests in flight at stop, ..."
```

## Failure responses

- Database stopped: a route that queries answers 503 with `Retry-After: 1`; `/healthz` and
  routes without queries keep answering 200.
- `sqlite:///app.db?queue_size=8` and a flood of slow queries: the excess answers 503 at once.
- A client trickling a request body slower than `request_timeout` gets 408 and is disconnected.

## Request ids in logs

```python
log = logging.getLogger("shop")

@app.get("/orders/{id}")
async def order(id: int):
    log.info("loading order %s", id)   # log line carries request_id=<x-request-id of the response>
```

`FERRO_LOG_FORMAT=json ferro run` → every stderr line is one JSON object
([contracts/logging.md](contracts/logging.md)).

## Metrics

```bash
FERRO_METRICS=true ferro run
curl http://127.0.0.1:8000/metrics      # from the same machine: Prometheus text format
```

From another machine (not in `metrics_allow`): 404.

## Automated validation

```bash
pytest tests/stress                       # reduced stress/failure/shutdown suite (< 2 min)
FERRO_STRESS=full pytest tests/stress     # full size; writes tests/benchmarks/results/011-stress.md
python tests/benchmarks/run_011.py        # metrics overhead (SC-005)
```

Expected: every scenario passes; the results file reports the numbers behind SC-001–SC-005.
