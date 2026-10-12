# Ferro

**A Python application framework with a Rust-native execution engine.**

You write ordinary Python (models, routes, handlers). Ferro runs them on a Rust server:
HTTP handling, routing, limits, JSON encoding and error handling all happen in Rust. You don't
need to know Rust to use it.

> Status: **0.5.0 release candidate** (alpha quality). Implemented: runtime core (001), Ferro's
> own ORM (002) on SQLite and PostgreSQL (003), Django-style migrations (004), relations,
> transactions and aggregation (005), middleware and security defaults (006), authentication
> (007), admin (008), CLI and project generator (009), multi-process serving (010), production
> hardening (011), the MVP acceptance shop [examples/ecommerce](examples/ecommerce/) (012),
> `select_for_update`, custom link models and abstract models (013), TLS and HTTP/2 (014),
> rate limits and metrics shared across processes plus `ferro.cache` (015), wheels and a release
> workflow (016), and the release-candidate work — security review, API freeze, deployment
> guide (017). See the [roadmap](docs/roadmap.md) and the [changelog](CHANGELOG.md).

```bash
pip install ferro        # once published; wheels for Linux, macOS and Windows, Python 3.12+
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

## Documentation

- [Documentation index](docs/README.md) — everything, by topic
- [Installation](docs/guide/01-installation.md) and the [tutorial](docs/guide/02-tutorial.md)
  (a notes API with users, an admin and tests)
- User guide: [routing](docs/guide/03-routing-requests-responses.md),
  [models and queries](docs/guide/04-models-and-queries.md),
  [migrations](docs/guide/05-migrations.md),
  [authentication](docs/guide/06-authentication.md), [admin](docs/guide/07-admin.md),
  [cache](docs/guide/08-cache.md), [security](docs/guide/09-security.md),
  [testing](docs/guide/10-testing.md), [logging and metrics](docs/guide/11-logging-monitoring.md)
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
uv pip install --python .venv maturin pytest httpx ruff trustme h2
uv run --python .venv maturin develop --uv
uv run --python .venv pytest
cargo test --workspace
```

## How it is built

The project follows spec-driven development with [Spec Kit](https://github.com/github/spec-kit):

- [docs/product-vision.md](docs/product-vision.md): product plan
- [.specify/memory/constitution.md](.specify/memory/constitution.md): non-negotiable principles
- [specs/001-runtime-core/](specs/001-runtime-core/) and
  [specs/002-orm-foundation/](specs/002-orm-foundation/): spec, plan, research, contracts and
  tasks for each feature

```text
crates/core           engine-independent types (settings, request/response, error envelope)
crates/router         path patterns and the routing trie
crates/http           HTTP engine adapter (the only crate that knows the engine)
crates/database       values, statements, errors, backend trait
crates/orm            model metadata, query AST, SQL compiler, DDL
crates/sqlite         SQLite backend (own thread pool)
crates/postgres       PostgreSQL backend (async driver, pool, TLS)
crates/migrations     schema state, diff, reversal and SQL for migrations
crates/shared         state shared by worker processes (rate-limit table; the only unsafe code)
crates/python-bridge  native module ferro._ferro (asyncio bridge, JSON, ORM bindings)
python/ferro          the Python API
```

## License

Licensed under either of [Apache License 2.0](LICENSE-APACHE) or [MIT](LICENSE-MIT) at your
option.
