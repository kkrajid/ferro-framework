<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/kkrajid/ferro-framework/main/docs/assets/ferro-logo-dark.png">
    <img src="https://raw.githubusercontent.com/kkrajid/ferro-framework/main/docs/assets/ferro-logo.png" alt="Ferro" width="260">
  </picture>
</p>

<p align="center"><strong>A Python application framework with a Rust-native execution engine.</strong></p>

<p align="center">
  <a href="https://pypi.org/project/ferro-framework/"><img src="https://img.shields.io/pypi/v/ferro-framework?include_prereleases" alt="PyPI"></a>
  <a href="https://pypi.org/project/ferro-framework/"><img src="https://img.shields.io/pypi/pyversions/ferro-framework" alt="Python versions"></a>
  <a href="https://github.com/kkrajid/ferro-framework/actions/workflows/ci.yml"><img src="https://github.com/kkrajid/ferro-framework/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="#license"><img src="https://img.shields.io/badge/license-MIT%20%2F%20Apache--2.0-blue" alt="License"></a>
</p>

You write ordinary Python (models, routes, handlers). Ferro runs them on a Rust server:
HTTP handling, routing, limits, JSON encoding and error handling all happen in Rust. You don't
need to know Rust to use it.

> Status: **0.6.0 release candidate** (alpha quality: a young project built by one person; try
> it, but do not bet production on it yet). The public API is frozen within a minor version
> ([stability policy](docs/stability.md)). See the [roadmap](docs/roadmap.md) and the
> [changelog](CHANGELOG.md).

```bash
pip install --pre ferro-framework   # wheels for Linux, macOS and Windows; CPython 3.12+
```

## Start a project

```bash
ferro new shop && cd shop
ferro makemigrations && ferro migrate
ferro dev
```

## Hello world

```python
# app.py
from ferro import App

app = App()


@app.get("/")
async def home():
    return {"message": "Hello from Rust"}
```

```bash
ferro dev
curl http://127.0.0.1:8000/
# {"message":"Hello from Rust"}
```

## What works today

- `@app.get/post/put/patch/delete/route` with `async def` or plain `def` handlers (plain ones
  run on a thread pool so they never stall the server)
- Path and query parameters injected by name, converted from `int`, `float`, `bool` and `str`
  type hints, with 422 errors listing every problem
- JSON request bodies (`await request.json()`) and responses (dicts, lists, `datetime`,
  `Decimal`, `UUID`), plus `str`, `bytes`, `None` (204) and explicit `Response` objects
- One JSON error format for every error. Production 500s never leak tracebacks; development
  mode shows them.
- Safe defaults: loopback bind, 1 MiB body limit, header and request timeouts, connection cap,
  security headers, request ids on every response, cross-origin (CSRF) protection, secure
  cookies; CORS and per-client rate limits one setting away — all enforced in Rust before Python
  runs
- `@app.middleware` (`async def mw(request, call_next)`), `request.state`, `request.cookies`,
  `Response.set_cookie`
- `ferro.auth`: users, groups, permissions, Argon2id passwords, database sessions,
  `login_required` / `permission_required`, `ferro createsuperuser`
- `ferro.admin`: `admin.register(Model, ModelAdmin(...))` + `app.add_admin(admin)` for list,
  search, filters, add/change/delete pages with per-model permissions
- `ferro dev` (debug + auto-reload), `ferro run` (production), `ferro routes`; settings
  through CLI options, `FERRO_*` environment variables or `App(...)` arguments
- Graceful shutdown on Ctrl+C / SIGTERM
- `ferro makemigrations` / `migrate` / `rollback` / `migration status` on SQLite and PostgreSQL
- Models with 12 field types, an async QuerySet (`filter`, `exclude`, `order_by`, slicing, `count`,
  `update`, `delete`, `bulk_create`, ...) and `ferro db create-tables`; SQL is compiled and run in
  Rust with bound parameters only
- `ForeignKey`, `OneToOne`, `ManyToMany` with lookups across relations, `select_related`,
  `prefetch_related` and reverse managers; `async with transaction()`; `aggregate`, `annotate`,
  `values`, `Q` and `F`

- `select_for_update()`, many-to-many through your own link model, abstract base models
- HTTPS and HTTP/2 from `ferro run --tls-cert ... --tls-key ...`, no proxy required
- `ferro run --processes N` with rate limits and metrics shared by every process; `ferro.cache`
  (memory or database)
- WebSockets (`@app.websocket`), server-sent events and streaming responses, and
  `ferro.broadcast` to reach sockets in every process and machine through PostgreSQL
- Pydantic models as request bodies and response models (`async def create(data: ProductIn)
  -> ProductOut`), validation errors as 422 with every problem listed
- FastAPI-style parameters: `Form`, `Header`, `Cookie`, `Depends` (nested, cached per request,
  generator cleanup, `app.dependency_overrides` for tests), `BackgroundTasks`
- Automatic OpenAPI 3.1 and Swagger UI (`app.add_openapi()`) with security schemes
  (`HTTPBearer`, `APIKeyHeader`, ...), `status_code`, `summary`, `responses` and tags
- File uploads (`UploadFile`; bodies over 1 MiB go to a temporary file, not memory),
  `FileResponse` with ranges and ETags, static files, `HTMLResponse`/`RedirectResponse`,
  Jinja2 templates, `app.url_for()`
- `ferro.jobs`: durable background jobs stored in your database, enqueued in your transaction,
  retried with backoff, run by `ferro jobs worker`
- Signals (`pre_save`, `post_save`, `m2m_changed`, request and login signals), `on_commit`,
  startup/shutdown hooks; `db.request_transaction` for one transaction per request
- More ORM: JSON key queries, date transforms, regex lookups, `Case`/`When`, subqueries,
  `distinct`, `get_or_create`, `iterator()`, raw SQL with bound parameters, isolation levels
- Site security: `allowed_hosts`, HTTPS redirect, HSTS, login throttling

## Performance

Measured on one Windows laptop (8 logical CPUs), release build, `oha` with 50 connections;
Ferro with 4 threads in one process, FastAPI on one uvicorn worker (httptools), Flask on
waitress with 8 threads. Treat these as an indication: client and servers share the machine,
and uvloop is not available on Windows.

| | Ferro | FastAPI (uvicorn) | Flask (waitress) |
|---|---|---|---|
| JSON hello world | 25,208 req/s | 2,685 req/s | 1,965 req/s |
| Pydantic body | 16,410 req/s | 2,175 req/s | 1,565 req/s |
| SQLite read | 12,458 req/s | 1,525 req/s | 1,514 req/s |
| Memory idle → peak under load | 46 → 49 MiB | 59 → 76 MiB | 55 → 56 MiB |

Full tables, latency percentiles and the scripts:
[throughput](tests/benchmarks/results/025-compare.md),
[memory](tests/benchmarks/results/026-memory.md). Run the comparison yourself on a fresh Linux
machine with the [Benchmark workflow](.github/workflows/benchmark.yml) (Actions → Benchmark →
Run workflow) or locally with `tests/benchmarks/run_025_compare.py` and `run_026_memory.py`.

## Documentation

- [Documentation index](docs/README.md) — everything, by topic
- [Installation](docs/guide/01-installation.md) and the [tutorial](docs/guide/02-tutorial.md)
  (a notes API with users, an admin and tests)
- User guide: [routing](docs/guide/03-routing-requests-responses.md),
  [models and queries](docs/guide/04-models-and-queries.md),
  [migrations](docs/guide/05-migrations.md),
  [authentication](docs/guide/06-authentication.md), [admin](docs/guide/07-admin.md),
  [cache](docs/guide/08-cache.md), [security](docs/guide/09-security.md),
  [testing](docs/guide/10-testing.md), [logging and metrics](docs/guide/11-logging-monitoring.md),
  [real-time: WebSockets, SSE, streaming](docs/guide/16-realtime.md),
  [Pydantic models](docs/guide/17-pydantic.md),
  [signals and hooks](docs/guide/18-signals-and-hooks.md),
  [forms, files, dependencies and OpenAPI](docs/guide/19-forms-files-dependencies-openapi.md),
  [background jobs](docs/guide/20-jobs.md)
- Production: [deployment](docs/deployment.md) (Ubuntu server from zero, Docker, Kubernetes,
  PaaS), [performance and scaling](docs/guide/12-performance-scaling.md),
  [troubleshooting](docs/guide/13-troubleshooting.md)
- Reference: [settings](docs/guide/14-settings-reference.md),
  [command line](docs/guide/15-cli-reference.md), [stability policy](docs/stability.md),
  [security policy](SECURITY.md), [changelog](CHANGELOG.md)

## Building from source

Requires Rust (stable), [uv](https://docs.astral.sh/uv/) and CPython 3.12+.

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv --group dev      # maturin, pytest, httpx, ruff, pydantic, ...
uv run --python .venv maturin develop --uv
uv run --python .venv pytest
cargo test --workspace
```

`maturin develop` makes an unoptimized debug build (quick to compile, 3–5 times slower; `ferro
run` warns about it). For benchmarks or serving real traffic from a checkout, use
`maturin develop --release`.

## Contributing

Contributions are welcome: bug reports, documentation fixes, tests, benchmarks on other
machines and features. Start with [CONTRIBUTING.md](CONTRIBUTING.md) (setup, tests, checks,
how a change goes from issue to pull request) and the [code of conduct](CODE_OF_CONDUCT.md).
Report security problems privately as described in [SECURITY.md](SECURITY.md).

## How it is built

The project follows spec-driven development with [Spec Kit](https://github.com/github/spec-kit):

- [docs/product-vision.md](docs/product-vision.md): product plan
- [.specify/memory/constitution.md](.specify/memory/constitution.md): non-negotiable principles
- [specs/](specs/): one folder per feature (001 to 026), with its spec and, for most, the plan,
  research, contracts and tasks

```text
crates/core           engine-independent types (settings, request/response, error envelope)
crates/router         path patterns and the routing trie
crates/http           HTTP engine adapter (the only crate that knows the engine)
crates/database       values, statements, errors, backend trait
crates/orm            model metadata, query AST, SQL compiler, DDL
crates/sqlite         SQLite backend (own thread pool)
crates/postgres       PostgreSQL backend (async driver, pool, TLS)
crates/migrations     schema state, diff, reversal and SQL for migrations
crates/shared         state shared by worker processes, memory maps (the only unsafe code)
crates/python-bridge  native module ferro._ferro (asyncio bridge, JSON, ORM bindings)
python/ferro          the Python API
```

## License

Licensed under either of [Apache License 2.0](LICENSE-APACHE) or [MIT](LICENSE-MIT) at your
option.

The native module includes open-source Rust crates (actix-web, tokio, PyO3, rustls and others),
all under permissive licences; their notices are in
[THIRD-PARTY-LICENSES.txt](THIRD-PARTY-LICENSES.txt), which ships in every wheel and the sdist.
