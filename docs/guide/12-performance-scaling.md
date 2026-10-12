# Performance and scaling

This page explains how Ferro uses CPUs and connections, which settings matter, how to size one
machine, and how to grow to many.

## How a request is handled

```text
client ──TCP/TLS──► engine I/O threads (Rust, `workers` per process)
                     │  parse, limits, routing, CORS/CSRF, rate limits, probes, metrics
                     ▼
                    Python event loop (one per process)
                     │  async handlers run here; `def` handlers on a thread pool (`sync_threads`)
                     ▼
                    database backend (Rust: SQLite pool or PostgreSQL connections)
                     │  queries run off the Python thread; results return to the loop
                     ▼
                    response serialized (JSON in Rust) ──► client
```

What this means:

- **Async handlers scale with waiting, not with threads.** While one handler awaits the database,
  the loop runs others. Thousands of concurrent requests need no extra threads.
- **Python code runs on one core per process.** Pure-Python work (loops, parsing, templating)
  is limited by that core. More cores need more **processes**.
- **The engine is not the bottleneck** for typical apps: the Rust engine alone served ~13,400
  requests/s on the development machine, a Python hello world ~9,900.

## The settings that matter

| Setting | Default | What it controls |
|---|---|---|
| `--processes` | 1 | Python interpreters (each with its own event loop, thread pool and database pool). Use for CPU-bound Python. |
| `--workers` | CPU count | engine I/O threads **per process**. With several processes use 1–2 each. |
| `--sync-threads` | 40 | threads for plain `def` handlers, per process |
| `--max-connections` | 10,000 | open client connections per server (split across workers) |
| `--request-timeout` | 30 s | cancels slow handlers (503) and slow uploads (408) |
| PostgreSQL `pool_size` | 10 | database connections **per process** |
| `queue_size` | 1024 | queries allowed to wait for a connection; beyond that they fail fast (503) instead of piling up |

## Sizing one machine

Start here, then measure (see [load testing](#load-testing)):

| Workload | Start with |
|---|---|
| Typical API/web app: async handlers, mostly database work | `--processes 2 --workers 2` on 2–4 CPUs; on bigger machines `--processes` ≈ CPUs / 2 |
| Handlers doing real Python work (rendering, parsing, computing) | `--processes` = number of CPUs, `--workers 1` or `2` |
| Very light handlers (proxies, tiny JSON) | `--processes 1` — extra processes cost more than they give |
| Containers / Kubernetes pods | `--processes 1` per pod and more replicas; let the orchestrator scale |

What we measured (8 logical CPUs, Windows, client on the same machine — your numbers will
differ; full results in [010-performance.md](../../tests/benchmarks/results/010-performance.md)):

| Scenario | 1 process | 4 processes |
|---|---|---|
| CPU-heavy Python route | 227 req/s | 694 req/s (3.1×) |
| database read by primary key (SQLite) | 5,761 req/s | 4,849 req/s (0.84×) |
| hello world | 9,851 req/s | 5,752 req/s (0.58×) |
| authenticated request (session lookup) | 2,321 req/s | — |

Lesson: processes multiply **Python CPU** capacity. They do not speed up work that waits on
the database or the network, and for near-empty handlers they cost throughput.

## The database

### Connections

Every process opens up to `pool_size` connections. Total connections:

```text
machines × processes per machine × pool_size  ≤  PostgreSQL max_connections − headroom
```

Example: 3 machines × 4 processes × 10 = 120 connections; PostgreSQL's default
`max_connections` is 100 — lower `pool_size` (`?pool_size=5` in the URL) or raise the server
limit. A connection pooler (PgBouncer) lets many app processes share fewer server connections;
Ferro uses prepared statements, so use PgBouncer 1.21+ with `max_prepared_statements` set
when running it in transaction mode, or session mode.

When the database is slow or down, queued queries are capped (`queue_size`) and fail fast with
`DatabaseUnavailableError` → 503 + `Retry-After`, and `/readyz` reports `database`, so the load
balancer can route around the problem instead of every request hanging.

### SQLite or PostgreSQL

SQLite is excellent for development, tests and small single-machine sites: reads are fast and
concurrent (WAL mode), but **one writer at a time** for the whole database. Use PostgreSQL when
you have several machines, heavy writes, or several processes writing at once.

### Queries

Most slow applications are slow in the database. In order of impact:

1. **Indexes** for every field you filter or order by often: `models.String(..., index=True)`,
   `unique=True`; foreign keys are indexed automatically.
2. **No N+1 queries.** `select_related("category")` (JOIN) and `prefetch_related("tags")` (one
   query per relation) instead of loading relations per row — Ferro raises `RelatedNotLoaded`
   rather than querying silently, so N+1 patterns show up in development.
3. **Fetch what you need.** `values("id", "name")` / `values_list("name", flat=True)` for lists;
   `count()`/`exists()` instead of loading rows.
4. **Let the database do the work.** `aggregate()`, `annotate()`, `update(stock=F("stock") - 1)`,
   `bulk_create()`, `bulk_update()`, `qs.delete()` run as single statements.
5. **Paginate.** Slicing gives `LIMIT/OFFSET`; for deep pages use keyset pagination:

   ```python
   page = await Order.objects.filter(pk__lt=last_seen_id).order_by("-pk")[:50]
   ```

6. **Short transactions.** Keep `async with transaction():` blocks small, especially on SQLite.

Check what runs with `qs.sql()`, and watch `ferro_db_query_duration_seconds`.

## Caching

- **Application cache**: `cache.get_or_set(key, compute, timeout=...)` around expensive
  queries and computations; `cache="database"` shares it across processes and machines
  ([Caching](08-cache.md)).
- **HTTP caching**: let browsers and CDNs reuse responses:

  ```python
  return Response(data, headers={"cache-control": "public, max-age=60"})
  ```

- **Static files** (images, CSS, JS): Ferro does not serve them. Put them on a CDN or object
  storage, or let the reverse proxy (Caddy/nginx) serve them from disk.

## Scaling out to several machines

Ferro applications are stateless when you keep state in the database:

| State | Where it lives | Works across machines |
|---|---|---|
| Users, sessions | database | ✅ any machine can serve any request (no sticky sessions) |
| `cache="database"` | database | ✅ |
| `cache="memory"`, module globals | one process | ❌ per process |
| Rate limits | shared memory of one machine | per machine — enforce global limits at the load balancer/API gateway |
| Metrics | per machine | ✅ scrape every machine; Prometheus sums |

Recipe:

1. Run the same version on N machines (or pods) behind a load balancer.
2. Health-check `/readyz`; liveness `/healthz`.
3. Use `--shutdown-drain 5` (with the load balancer's health check interval below the drain)
   so rolling deploys drop no requests.
4. Run `ferro migrate` once per release, before the new version takes traffic.
5. Size the database connections with the formula above.
6. Set `FERRO_TRUSTED_PROXIES` to the load balancer's addresses.

On Kubernetes, scale on CPU with a HorizontalPodAutoscaler; one process per pod keeps the
signal clear. See the [deployment guide](../deployment.md#4-kubernetes).

Beyond one database: read replicas and sharding are not built in; scale PostgreSQL vertically
first (it goes a long way), then add caching, then consider splitting workloads.

## Load testing

1. Test a production-like setup: `ferro run` (never `ferro dev`), PostgreSQL, the same number of
   processes, a separate load-generating machine.
2. Use a load tool such as [oha](https://github.com/hatoo/oha), [k6](https://k6.io) or
   [Locust](https://locust.io):

   ```bash
   oha -z 30s -c 100 http://10.0.1.5:8000/api/products
   ```

3. Raise concurrency until latency (p99) rises or errors appear; note requests/s at that point.
4. Watch `/metrics` while testing: `ferro_http_requests_in_flight`, request and query durations,
   `ferro_db_queries_total{outcome="unavailable"}`.
5. Change one setting at a time (processes, workers, pool size) and compare. Throughput on a
   busy machine varies run to run; repeat runs and compare medians, or compare server CPU time
   per request.

## Checklist for a slow endpoint

- [ ] Is it the database? Look at `ferro_db_query_duration_seconds` and `qs.sql()`; add an
      index; remove N+1 with `select_related`/`prefetch_related`.
- [ ] Is it Python CPU? One core at 100% while others idle → add `--processes`.
- [ ] Is it waiting on another service? Use async clients (e.g. `httpx.AsyncClient`) so the event
      loop keeps serving others; never call blocking libraries from `async def` handlers (use a
      plain `def` handler instead, which runs on the thread pool).
- [ ] Can the result be cached?
- [ ] Are responses large? Paginate; return only needed fields.
