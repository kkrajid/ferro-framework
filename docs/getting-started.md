# Ferro in one page

A quick tour of everything Ferro does. For step-by-step learning start with the
[tutorial](guide/02-tutorial.md); for details on each topic see the
[documentation index](README.md).

## 0. Start a project

```bash
ferro new shop                             # app.py, models.py, admin.py, tests/, .env, ...
cd shop
ferro makemigrations && ferro migrate      # the database URL comes from .env
FERRO_SUPERUSER_PASSWORD="a-long-secret" ferro createsuperuser --username admin --no-input
ferro dev                                  # http://127.0.0.1:8000/ and /admin/
```

The generated project runs and passes its own tests (`python -m pytest`, using
`ferro.testing.TestClient`) without edits. Every `ferro` command reads `./.env` (`KEY=VALUE`
lines); variables already set in the environment win.

```bash
ferro shell                                # Python with top-level await, app and models loaded
>>> await Product.objects.count()
ferro shell -c "print(await Product.objects.count())"
ferro db shell                             # sqlite3 or psql connected to the configured database
```

The sections below build an application by hand to show each part.

A complete example — categories, products, orders, payments, a JSON API and the admin, on
SQLite or PostgreSQL — is [examples/ecommerce](../examples/ecommerce/).

## 1. A first application

```python
# app.py
from ferro import App

app = App()


@app.get("/")
async def home():
    return {"message": "Hello from Rust"}
```

Run `ferro dev` in the same directory. The server listens on `http://127.0.0.1:8000`, logs
each request and restarts when you save a `.py` file.

## 2. Routes and handlers

```python
@app.get("/items")
@app.post("/items")
@app.put("/items/{id}")
@app.patch("/items/{id}")
@app.delete("/items/{id}")
@app.route("/both", methods=["GET", "POST"])
```

- `HEAD` is answered automatically for every `GET` route.
- A path with the wrong method gets `405` with an `allow` header.
- `/items/` redirects (308) to `/items` when only `/items` exists, and the reverse also holds.
- Static segments beat parameters: `/users/me` wins over `/users/{id}` whatever the order.
- Handlers may be `async def` (preferred) or plain `def`. Plain handlers run on a thread pool
  (`sync_threads`, default 40), so a blocking call never stalls other requests.

## 3. Parameters

Handler parameters are filled by name:

```python
@app.get("/products/{id}")
async def product(request, id: int, currency: str = "INR", in_stock: bool | None = None):
    ...
```

| Parameter | Receives |
|---|---|
| `request` (or annotated `Request`) | the request object |
| a name in the path (`id`) | the path value |
| any other name | the query parameter of that name; its default if absent |

`int`, `float`, `bool` (`true/false`, `1/0`, `yes/no`, `on/off`) and `str` hints are converted.
Bad or missing values produce one `422` listing every problem:

```json
{"error": {"status": 422, "message": "Validation failed", "request_id": "…",
  "details": [{"param": "id", "location": "path", "problem": "expected int, got 'abc'"}]}}
```

## 4. The request

```python
request.method, request.path, request.raw_path, request.query_string
request.query.get("tag"), request.query.getlist("tag")
request.headers.get("user-agent")          # case-insensitive
request.path_params, request.client, request.request_id
body = await request.body()                 # bytes
data = await request.json()                 # 400 if not JSON
```

Bodies larger than `max_body_size` (default 1 MiB) are rejected with `413` before your handler
runs.

## 5. Responses

| Return value | Response |
|---|---|
| `dict`, `list`, `tuple` | JSON (`datetime`, `Decimal`, `UUID` become strings) |
| `str` | `text/plain; charset=utf-8` |
| `bytes` | `application/octet-stream` |
| `None` | `204 No Content` |
| `Response(content, status=, headers=, media_type=)` | exactly that |
| `JSONResponse(content, status=, headers=)` | always JSON |

## 6. Errors

```python
from ferro import HTTPException

raise HTTPException(404, "Order not found")
```

Every error uses the same envelope: `{"error": {"status", "message", "request_id"}}`. An
unexpected exception produces a `500`. In production (`ferro run`) the body contains only the
generic message and the request id; the traceback goes to the server log under the same id. In
development (`ferro dev`) the body also includes the exception type, message and traceback.

A handler running longer than `request_timeout` (default 30 s) is cancelled, and the client
gets `503`.

## 7. Commands and settings

```bash
ferro dev    [module:app] [options]   # debug on, auto-reload
ferro run    [module:app] [options]   # production
ferro routes [module:app]             # print the route table
```

| Setting | Option | Environment | Default |
|---|---|---|---|
| host | `--host` | `FERRO_HOST` | `127.0.0.1` |
| port | `--port` | `FERRO_PORT` | `8000` |
| workers (I/O threads) | `--workers` | `FERRO_WORKERS` | CPU count |
| debug | `--debug/--no-debug` | `FERRO_DEBUG` | on for `dev`, off for `run` |
| max_body_size | `--max-body-size` | `FERRO_MAX_BODY_SIZE` | 1 MiB |
| request_timeout | `--request-timeout` | `FERRO_REQUEST_TIMEOUT` | 30 s |
| shutdown_timeout | `--shutdown-timeout` | `FERRO_SHUTDOWN_TIMEOUT` | 30 s |
| header_timeout | `--header-timeout` | `FERRO_HEADER_TIMEOUT` | 5 s |
| max_connections | `--max-connections` | `FERRO_MAX_CONNECTIONS` | 10,000 |
| sync_threads | `--sync-threads` | `FERRO_SYNC_THREADS` | 40 |
| processes | `--processes` (`run` only) | `FERRO_PROCESSES` | 1 |
| log_level | `--log-level` | `FERRO_LOG_LEVEL` | `info` |
| log_format | `--log-format` | `FERRO_LOG_FORMAT` | `text` (or `json`) |
| shutdown_drain | `--shutdown-drain` | `FERRO_SHUTDOWN_DRAIN` | 0 s |
| health_path / ready_path | — | `FERRO_HEALTH_PATH` / `FERRO_READY_PATH` | `/healthz` / `/readyz` |
| metrics / metrics_path / metrics_allow | — | `FERRO_METRICS` / `FERRO_METRICS_PATH` / `FERRO_METRICS_ALLOW` | off / `/metrics` / `127.0.0.1,::1` |
| tls_cert / tls_key | `--tls-cert` / `--tls-key` | `FERRO_TLS_CERT` / `FERRO_TLS_KEY` | off (plain HTTP) |
| cache | — | `FERRO_CACHE` | `memory` (or `database`) |

Precedence: command line > environment > `App(...)` argument > default.

Exit codes: `0` clean shutdown, `1` runtime failure, `2` configuration or usage error, `3`
address already in use.

### Using every CPU: `--processes`

Python handlers in one process share one interpreter, so CPU-bound Python code uses one core.
`ferro run --processes 4` binds the port once and starts four worker processes that share it;
each has its own interpreter, I/O threads (`--workers` is per process) and database pool.

```bash
ferro run --processes 4 --workers 2
# Ferro 0.5.0rc2 serving app:app on http://127.0.0.1:8000 (production mode, 4 processes, 2 workers each)
```

- A worker that crashes is restarted; workers that keep failing right after starting (an
  import error, say) stop the command with that error (exit 1).
- Ctrl+C / SIGTERM stops every worker gracefully; a second Ctrl+C stops them at once.
- Rate limits are shared: a client gets the configured budget whichever worker serves it
  (a shared-memory table private to the command). `/metrics` from any worker reports totals
  over all workers (other workers' numbers are at most a second old; counts of a worker that
  died stay in the totals). Module-level variables and the `memory` cache exist once per
  process; use `cache="database"` (below) for a cache every worker shares. Sessions, users and
  data live in the database and work in every worker.
- `ferro dev` is always one process (auto-reload); `App.run()` too — use `ferro run`.

When it pays off: processes help handlers that spend time in Python (on an 8-CPU machine a
CPU-heavy route served about 3× the requests with 4 processes, in every run). A SQLite read
route gained nothing reliable (0.84× to 1.36× across runs). For near-empty handlers processes
cost more than they give: hello world served about half to two-thirds as many requests with 4
processes as with one, most likely because each process's Python thread is woken for fewer
requests at a time. Measure your own application; numbers are in
[tests/benchmarks/results/010-performance.md](../tests/benchmarks/results/010-performance.md).

### Running in production

**Probes.** Every server answers `GET /healthz` (`{"status": "ok"}`: the process is alive) and
`GET /readyz` (`{"status": "ready"}`, or 503 `{"status": "not ready", "failing": [...]}` while
the database does not answer or the server is shutting down) without running your code. Point
your load balancer or orchestrator at them. The database check is `SELECT 1` with a 750 ms limit,
reused for a second however many probes arrive. Move them with `health_path` / `ready_path`, or
disable one with an empty value; a route on a probe address is a startup error.

**Deploys without dropped requests.** With `--shutdown-drain 5`, a stop signal first makes
`/readyz` fail for 5 seconds while requests are still served, so the load balancer stops sending
traffic; then the server stops accepting connections and lets requests in flight finish within
`shutdown_timeout`. A second signal stops at once. The last log line says what happened:
`stopped: 12 requests in flight at stop, 12 finished, 0 cut off after shutdown_timeout`.

**Request ids in your logs.** Log records written while a request is handled carry its id
(the `x-request-id` response header) — from async and sync handlers alike:

```python
import logging

log = logging.getLogger("shop")

@app.post("/orders")
async def create_order(request):
    log.info("creating order")   # INFO ferro.python: creating order logger=shop request_id=3f2a…
```

If your application configures logging itself, Ferro leaves it alone and `%(request_id)s` works
in your formats. `--log-format json` writes every log line (access, errors, your own) as one JSON
object.

**When the database is down.** An ORM call that cannot reach the database (no connection, lost
connection, lock wait expired, too many queued queries) raises `DatabaseUnavailableError` (a
kind of `OperationalError`). Unhandled, it becomes a 503 with `Retry-After: 1` instead of a 500,
and one warning line with the request id; catch it to serve something degraded. Queued queries are
bounded (`?queue_size=1024` in the database URL by default); beyond that, queries fail at once
rather than making every other request wait.

**Slow clients.** `request_timeout` covers receiving the request body as well as running the
handler: a client still uploading when it expires gets 408 and is disconnected.

**Metrics.** `FERRO_METRICS=1` serves Prometheus-format metrics at `/metrics` — requests by
route, method and status class, latency histograms, requests in flight, database queries by
outcome and their latency — to `metrics_allow` addresses only (the local machine by default;
others get 404). Metrics are per process.

**HTTPS and HTTP/2 without a proxy.** Give the server a certificate chain and its private key
(PEM files, e.g. from Let's Encrypt's `fullchain.pem` and `privkey.pem`):

```bash
ferro run --host 0.0.0.0 --port 443 --tls-cert /etc/ferro/fullchain.pem --tls-key /etc/ferro/privkey.pem
# Ferro 0.5.0rc2 serving app:app on https://0.0.0.0:443 (production mode, 8 workers)
```

The listener then speaks TLS 1.2/1.3 only and offers HTTP/2 next to HTTP/1.1 (ALPN; clients
choose). Everything else — probes, metrics, limits, timeouts, `--processes`, graceful shutdown —
works the same. Requests are treated as `https` (origin checks), and HTTP/2 requests show their
authority as the `host` header. A missing or unreadable file, invalid PEM or a key that does not
match the certificate stops the command before it listens (exit 2), naming the setting and the
file. Certificates are read at start: after renewing them, restart the server (with
`--shutdown-drain` behind a load balancer). Turn on `hsts` once HTTPS works. Plain HTTP on the
same port is not answered; to redirect port 80, or to terminate TLS for several applications,
obtain certificates automatically or serve static files, a reverse proxy (Caddy, nginx) in front
of plain `ferro run` is still the simpler choice.

## 8. Models and the ORM

Ferro has its own ORM: SQL is generated and run by the Rust core, and values are always sent as
bound parameters. SQLite and PostgreSQL are supported with the same models and queries.

```python
from datetime import UTC, datetime
from decimal import Decimal
from ferro import App, models

app = App(database_url="sqlite:///shop.db")


class Product(models.Model):
    name = models.String(max_length=200, index=True)
    sku = models.String(max_length=40, unique=True)
    price = models.Decimal(max_digits=10, decimal_places=2)
    stock = models.Integer(default=0)
    created = models.DateTime(default=lambda: datetime.now(UTC))

    class Meta:
        table = "products"          # optional; default is the snake_case class name


@app.get("/products/{id}")
async def product(id: int):
    return (await Product.objects.get_or_404(id=id)).to_dict()
```

Create and evolve the schema with migrations, as in Django:

```bash
ferro makemigrations                                  # writes migrations/0001_initial.py
ferro migrate --database-url sqlite:///shop.db        # applies it
# change a model, then:
ferro makemigrations                                  # migrations/0002_add_product_sku.py
ferro migrate --database-url sqlite:///shop.db
ferro migration status --database-url sqlite:///shop.db
ferro rollback --database-url sqlite:///shop.db       # revert the last migration
```

Migration files are plain Python you can read and edit (`ops.CreateTable`, `ops.AddColumn`,
`ops.AlterColumn`, `ops.RenameColumn`, ...). Each migration runs in one transaction on SQLite
and PostgreSQL. Adding a non-null field to an existing table needs a constant `default` (it
back-fills existing rows). A renamed field is generated as remove + add with a warning: change
it to `ops.RenameColumn(...)` to keep the data. `ferro makemigrations --check` exits 1 when
models and migrations differ (useful in CI). Without migrations, `ferro db create-tables`
still creates missing tables for quick experiments.

| Task | Code |
|---|---|
| create | `p = await Product.objects.create(name="Laptop", sku="L1", price=Decimal("75000.00"))` |
| read one | `await Product.objects.get(pk=1)` (`Product.DoesNotExist` if missing) |
| list | `await Product.objects.filter(price__gte=1000).exclude(stock=0).order_by("-price")` |
| page | `await Product.objects.order_by("name")[20:40]` |
| count / exists | `await qs.count()`, `await qs.exists()`, `await qs.first()`, `await qs.last()` |
| change | `p.stock = 5; await p.save()` or `await qs.update(stock=0)` |
| delete | `await p.delete()` or `await qs.delete()` |
| bulk | `await Product.objects.bulk_create(objs)`, `await Product.objects.bulk_update(objs, fields=["price"])` |

Lookups: `exact`, `iexact`, `contains`, `icontains`, `startswith`, `endswith`, `gt`, `gte`, `lt`,
`lte`, `in`, `isnull`. Fields: `String`, `Text`, `Integer`, `BigInteger`, `Float`, `Decimal`,
`Boolean`, `Date`, `DateTime` (timezone-aware, stored as UTC), `Time`, `UUID`, `JSON`; options
`null`, `default`, `unique`, `index`, `primary_key`, `column`.

Outside the server (scripts, tests): `await ferro.db.connect(url)`, `await ferro.db.create_tables()`,
`await ferro.db.disconnect()`. `qs.sql()` shows the SQL and its parameters.

On SQLite, `iexact`/`icontains` fold ASCII letters only, and `Decimal` keeps 15 significant digits
exactly.

### PostgreSQL

```bash
ferro dev --database-url "postgresql://shop:secret@localhost:5432/shop"
```

URL parameters: `sslmode` (`disable`, `prefer` (default), `require`, `verify-full`; certificates
are always verified when TLS is used), `sslrootcert=/path/ca.pem`, `pool_size` (default 10),
`connect_timeout`, `options` (e.g. `-csearch_path=tenant1`), `application_name`. Columns use
native types (`numeric`, `timestamp with time zone`, `uuid`, `jsonb`, ...), decimals are exact up
to 38 digits, and case-insensitive lookups fold Unicode letters. Connections that break (for
example after a server restart) are reopened on the next query. Passwords never appear in error
messages. NULLs sort last in ascending order on PostgreSQL and first on SQLite.

## 9. Relations, transactions and aggregation

```python
from ferro import models


class Category(models.Model):
    name = models.String(max_length=100)
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, related_name="children")


class Tag(models.Model):
    name = models.String(max_length=30, unique=True)


class Product(models.Model):
    name = models.String(max_length=200)
    price = models.Decimal(max_digits=10, decimal_places=2)
    stock = models.Integer(default=0)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    tags = models.ManyToMany(Tag, related_name="products")


class Profile(models.Model):
    customer = models.OneToOne("Customer", on_delete=models.CASCADE, related_name="profile")
```

`makemigrations` creates `category_id` with a foreign key constraint and an index, and a link
table `product_tags` for the many-to-many relation. `on_delete`: `CASCADE`, `SET_NULL` (needs
`null=True`), `PROTECT` (raises `ProtectedError`) or `DO_NOTHING`; the database applies it, so
`obj.delete()` and `queryset.delete()` behave the same. A target may be a class, `"self"` or a
model name defined later.

```python
phone = await Product.objects.create(name="X1", price=Decimal("499"), category=phones)
phone.category_id                                  # always available
phone.category                                     # the instance (assigned above)

await Product.objects.filter(category__name="Phones", tags__name="sale")
await Category.objects.filter(products__price__gt=1000)        # each category once
products = await Product.objects.select_related("category")    # one query, LEFT JOIN
products = await Product.objects.prefetch_related("tags")      # one more query per level
await phone.fetch_related("category")                          # load on one instance

await phones.products.filter(stock=0).count()      # reverse manager (`product_set` by default)
await phones.products.create(name="X2", price=Decimal("599"))
await phone.tags.add(sale, new)                    # also remove(), clear(), set([...])
```

Reading a relation that was not loaded raises `RelatedNotLoaded` instead of running a hidden
query. Conditions on many-valued relations inside one `filter()` call must match the same related
row; separate `filter()` calls may match different rows (Django's rule).

Transactions:

```python
from ferro.db import transaction
from ferro.models import F

async with transaction():
    order = await Order.objects.create(customer=customer)
    sold = await Product.objects.filter(pk=phone.pk, stock__gte=2).update(stock=F("stock") - 2)
    if not sold:
        raise OutOfStock()                         # rolls everything back
    await OrderItem.objects.create(order=order, product=phone, quantity=2)
```

`@transaction()` decorates an `async def`. Nested blocks are savepoints. Queries of tasks started
inside the block join the transaction; other requests never see uncommitted rows. Cancellation
rolls back. On SQLite a transaction holds the write lock from its start (`BEGIN IMMEDIATE`). On
PostgreSQL a failed query aborts the transaction; wrap it in a nested `transaction()` to recover.

Locking rows you are about to change (read, check, write):

```python
async with transaction():
    product = await Product.objects.select_for_update().get(pk=pk)
    if product.stock < quantity:
        raise OutOfStock()
    product.stock -= quantity
    await product.save()
```

Other transactions locking or changing those rows wait until this one ends.
`select_for_update(nowait=True)` fails at once with `DatabaseUnavailableError` instead of waiting;
`select_for_update(skip_locked=True)` leaves locked rows out (job queues). Only the model's own
rows are locked, not `select_related` ones. Evaluating it outside `transaction()` raises
`TransactionError`; `count()`, `exists()`, `aggregate()` and `annotate()` refuse it. On PostgreSQL
the query ends with `FOR UPDATE OF <table>`; on SQLite nothing is added, as a transaction already
holds the database write lock.

Link models with data of their own:

```python
class Band(models.Model):
    name = models.String(max_length=50)
    members = models.ManyToMany("Musician", through="Membership", related_name="bands")


class Membership(models.Model):
    band = models.ForeignKey(Band, on_delete=models.CASCADE)
    musician = models.ForeignKey("Musician", on_delete=models.CASCADE)
    role = models.String(max_length=20)
    since = models.Date(null=True)


await band.members.add(ann, through_defaults={"role": "guitar"})
await band.members.set([ann, bob], through_defaults={"role": "vocals"})   # new links only
await Musician.objects.filter(bands__name="Loud", membership__role="guitar")
```

No automatic link table is created; `makemigrations` creates `Membership`'s. The link model needs
one foreign key to each side; with more (or for `"self"`), name them:
`ManyToMany("self", through="Follow", through_fields=("follower", "followed"))`. Adding an existing
pair adds nothing; a required extra field without a value in `through_defaults` raises
`FieldError`. The admin shows such relations read-only; edit the link rows on the link model's page.

Abstract base models share fields, relations, methods and `Meta` options:

```python
class Timestamped(models.Model):
    created = models.DateTime(default=now)
    updated = models.DateTime(default=now)

    class Meta:
        abstract = True


class Owned(Timestamped):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="%(class)s_set")

    class Meta:
        abstract = True
        unique_together = [("owner", "slug")]


class Note(Owned):
    slug = models.String(max_length=50)
    body = models.Text()


class Photo(Owned):
    slug = models.String(max_length=50)
    url = models.String(max_length=500)
    updated = None                                   # drop an inherited field

    class Meta(Owned.Meta):                          # extend the inherited options
        table = "photos"
```

Each concrete model gets its own copy of the inherited fields (parents first) and its own table;
abstract models have no table and cannot be queried. `%(class)s` in `related_name` becomes the
model's snake_case name (`note_set`, `photo_set`). A model without its own `Meta` inherits its
base's (except `table`). Redefining a field replaces it. Inheriting from a concrete model
(multi-table or proxy inheritance) is not supported and raises `FieldError`.

Caching expensive results:

```python
from ferro.cache import cache

app = App(cache="database")          # shared by every process and server; default "memory"

@app.get("/")
async def home():
    return await cache.get_or_set("home", build_home, timeout=60)   # build_home: sync or async

await cache.set("rates", {"usd": 83.1}, timeout=300)   # seconds; None = no expiry
await cache.get("rates", default={})
await cache.add("lock:42", True, timeout=10)          # only if absent: True/False
await cache.incr("visits")                            # atomic, also across processes
await cache.delete("rates"); await cache.clear()
```

`memory` keeps up to 10,000 entries per process (least recently used evicted); `database` uses
the `ferro_cache` table — `App(cache="database")` makes `makemigrations` create it. Values must be
JSON-compatible and come back as JSON (tuples become lists); other values raise `TypeError`.
Scripts and tests pick a backend with `ferro.cache.configure("database")` (or
`configure("memory", max_entries=100)`), which the server then leaves alone.

Aggregation, `Q` and `F`:

```python
from ferro.models import Avg, Count, F, Max, Q, Sum

await Product.objects.aggregate(total=Sum("price"), n=Count(), avg=Avg("price"))
await Category.objects.annotate(n=Count("products")).filter(n__gt=5).order_by("-n")
await Product.objects.values("category__name").annotate(stock=Sum("stock"))   # one row per group
await Product.objects.values_list("name", flat=True)
await Product.objects.filter(Q(price__lt=100) | Q(tags__name="sale"), ~Q(stock=0))
await Product.objects.filter(price__gt=F("cost") * 2)
```

`Avg` returns `float`; `Sum`, `Min` and `Max` keep the field's type; aggregates over no rows return
`None` (`Count` returns 0) unless `default=` is given. `Meta.unique_together = [("cart",
"product")]` adds a composite unique index. `bulk_create(..., ignore_conflicts=True)` skips
duplicates.

## 10. Middleware and security

```python
import time

from ferro import App, CORS, Response

app = App(
    cors=CORS(allow_origins=["https://app.example.com"], allow_credentials=True),
    rate_limit="100/minute",          # per client address; off by default
    trusted_proxies=["10.0.0.0/8"],   # honour x-forwarded-for from these
)


@app.middleware
async def timing(request, call_next):
    started = time.perf_counter()
    response = await call_next(request)        # always a Response
    response.headers.append(("x-elapsed-ms", f"{(time.perf_counter() - started) * 1000:.1f}"))
    return response


@app.post("/login", rate_limit="5/minute")
async def login(request):
    request.cookies                             # {"name": "value"}
    response = Response({"ok": True})
    response.set_cookie("sid", "abc123", max_age=3600)
    return response
```

Middleware runs in registration order (the first is outermost) for requests that reach a route;
`request.state` carries values to the handler. Without middleware there is no extra cost.

The engine applies these before any Python runs:

- **Cross-origin protection (CSRF)**, on by default: POST/PUT/PATCH/DELETE requests a browser
  sends from another site (`Sec-Fetch-Site: cross-site`, or an `Origin` that is not this site)
  get 403. Clients that send neither header (scripts, mobile apps) are not affected. Allow other
  sites with `trusted_origins=[...]` or CORS, exempt a route with `@app.post(..., csrf=False)`
  (webhooks), or turn it off with `csrf=False`.
- **CORS**: off until configured; preflights are answered by the engine.
- **Rate limits**: `N/second|minute|hour|day`, app-wide or per route; 429 with `retry-after`.
- **Headers** on every response unless the handler sets them: `x-content-type-options: nosniff`,
  `referrer-policy`, `x-frame-options: DENY`, `cross-origin-opener-policy: same-origin`; HSTS
  with `hsts=True` (behind TLS) and `content_security_policy="..."` when configured.
- **Cookies** set with `set_cookie` are `HttpOnly`, `SameSite=Lax`, `Path=/` and — outside
  `ferro dev` — `Secure` unless you say otherwise.

Settings can also come from the environment: `FERRO_RATE_LIMIT`, `FERRO_TRUSTED_PROXIES`,
`FERRO_TRUSTED_ORIGINS` (comma separated), `FERRO_CSRF`, `FERRO_HSTS`,
`FERRO_CONTENT_SECURITY_POLICY`.

## 11. Authentication

```python
from ferro import App, HTTPException, auth
from ferro.auth import User, authenticate, login_required, permission_required

app = App()
app.add_auth()        # request.user and request.session on every routed request


@app.post("/login", rate_limit="5/minute")
async def sign_in(request):
    data = await request.json()
    user = await authenticate(data["username"], data["password"])
    if user is None:
        raise HTTPException(401, "Invalid credentials")
    await auth.login(request, user)
    return {"ok": True}


@app.post("/logout")
async def sign_out(request):
    await auth.logout(request)
    return {"ok": True}


@app.get("/me")
@login_required                      # 401 for anonymous users
async def me(request):
    request.session["visits"] = request.session.get("visits", 0) + 1
    return {"username": request.user.username, "visits": request.session["visits"]}


@app.put("/products/{id}")
@permission_required("change_product")   # 403 without the permission
async def update_product(request, id: int): ...
```

```bash
ferro makemigrations && ferro migrate      # auth tables + add/change/delete/view permissions
FERRO_SUPERUSER_PASSWORD="a-long-secret" ferro createsuperuser --username admin --email a@x.io
```

- `User.objects.create_user(...)` / `create_superuser(...)`; `await user.set_password(...)`,
  `check_password(...)`, `has_perm("change_product")`, `has_perms([...])`; `Group` with
  `permissions`, `user.groups`, `user.user_permissions` (many-to-many managers).
- Passwords are hashed with Argon2id in Rust on a separate thread pool (other requests keep
  being served); weak passwords (under 8 characters, all digits, equal to the username) raise
  `PasswordError` (422). Unknown users take as long to reject as wrong passwords.
- Sessions live in the database (`auth_session`); the `sessionid` cookie holds a random key and
  the table only its SHA-256 digest. Logging in rotates the key; sessions last 14 days, renewed
  while used, and a login ends after 30 days or when the user's password changes (after a user
  changes their own password, `await auth.login(request, user)` keeps them signed in). Visitors
  get a stored session only once something is put in `request.session`; expired sessions are
  deleted when someone logs in. An authenticated request costs one query.
- Password checks wait in a bounded queue (32 per hashing thread): a login flood beyond that is
  answered 503 with `Retry-After` instead of piling up work.
- Permission names are `<action>_<table>` (`add_product`, `view_order`); `ferro migrate` creates
  them for every model. Superusers have all permissions, inactive users none.

## 12. Admin

```python
from ferro.admin import ModelAdmin, admin

admin.register(Category)
admin.register(Product, ModelAdmin(
    list_display=["name", "price", "category", "active"],
    search_fields=["name", "category__name"],
    list_filter=["category", "active"],
    ordering=["-price"],
    readonly_fields=["created"],
))
app.add_admin(admin)          # /admin (also installs authentication)
```

```bash
FERRO_SUPERUSER_PASSWORD="a-long-secret" ferro createsuperuser --username admin --no-input
ferro dev                     # http://127.0.0.1:8000/admin/
```

Active staff users log in at `/admin/login` (rate limited to 10 attempts a minute). Each model
has a list page (columns, search, filters, ordering, 50 rows per page), add and change forms
generated from the fields (select lists for foreign keys, multi-selects for many-to-many,
validation messages next to the fields) and a delete page that names the rows deleted with it or
explains why a protected row cannot be deleted. Staff users without `view_`/`add_`/`change_`/
`delete_<table>` permissions only see what they are allowed to; superusers see everything.
`admin.register(User)` gives a write-only password field (hashed with `set_password`, left
unchanged when empty) and lets only superusers change `is_superuser`, groups and permissions.

Pages are plain HTML forms without JavaScript; every value is escaped, and pages are sent with a
strict Content-Security-Policy and `cache-control: no-store`. Handlers can read the same kind of
form data with `await request.form()` (`get`, `getlist`).

## 13. Current limits

Within a process, Python handlers share one interpreter (use `ferro run --processes N` for more
CPUs). Rate limits and metrics are shared by the processes of one machine, not across machines;
the `memory` cache is per process. TLS certificates are read at start (no hot reload, no
automatic certificates) and HTTP/2 needs TLS (no h2c). A query already running on the database is
not interrupted when its request is cancelled; it finishes on its own connection. Symmetrical
many-to-many, multi-table and proxy model inheritance, and locking rows of joined models are not
available.
