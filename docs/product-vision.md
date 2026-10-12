# Ferro — Product Vision & MVP Plan

> Source document for all Spec Kit features. Specs in `specs/` MUST trace back to a section here.
> Product name: **Ferro**. Python package: `ferro`. CLI command: `ferro`.

## 1. Product Vision

Build a modern Python web framework that provides:

- Django-like simplicity
- FastAPI-like API development experience
- Actix Web-powered HTTP performance
- Rust-powered runtime and core infrastructure
- A completely framework-owned ORM
- Django-like migrations
- Built-in authentication
- Built-in admin panel
- Secure-by-default behavior
- Strong concurrency and stability
- Production-ready PostgreSQL support
- SQLite support for development and small applications

The framework must feel like a normal Python framework to the developer. The developer must not
need to understand Rust, Actix, Tokio, PyO3, or the internal runtime.

## 2. Core Philosophy

**Python for developer experience. Rust for infrastructure and performance.**

Python primarily defines: models, routes, controllers/views, business logic, configuration,
application behavior.

Rust primarily handles: HTTP server, request parsing, routing engine, middleware execution,
connection pooling, ORM query engine, SQL generation, serialization, schema management, migration
engine, security primitives, concurrency, runtime infrastructure.

The goal is not to rewrite Django in Rust. The goal is a new architecture where the Python layer is
intentionally thin.

## 3. High-Level Architecture

```text
                    Python Application
                           │
        ┌──────────────────┼──────────────────┐
      Models             Routes             Views
        └──────────────────┼──────────────────┘
                     Python API Layer
                           │
                      PyO3 Bridge
                           │
                       Rust Core
                           │
        ┌──────────────────┼──────────────────┐
     Actix Web          ORM Engine         Runtime
     HTTP/HTTPS        Query Builder       Tokio
     Routing           SQL Generator       Async I/O
     Middleware        Transactions        Workers
        └──────────────┬───┘
                Database Drivers
              ┌────────┴─────────┐
          PostgreSQL           SQLite
```

Actix Web provides the server foundation, request processing, routing and middleware primitives,
so engineering effort goes to the Python bridge, ORM, migrations and developer experience.

### Binding architecture decisions

1. **Actix is an internal dependency, never a public API.** No Actix type may appear in the Python
   API or in any public Rust crate interface consumed by the Python layer. Swapping Actix for
   another Rust HTTP engine later must not break the Python API.
2. **The ORM is framework-owned.** Pipeline: Model → Query AST → SQL compiler → DB backend.
   Do not wrap Django ORM, SQLAlchemy, Tortoise, Diesel or SeaORM. Existing ORMs (e.g. Diesel) are
   design references only, not implementation dependencies.

## 4. MVP Scope

**Core:** project creation CLI, application configuration, development server, production server,
routing, path parameters, query parameters, request/response objects, JSON responses, JSON request
parsing, middleware, error handling, environment configuration.

**ORM:** model declaration, field system, primary keys, default values, nullable fields, unique
constraints, indexes, foreign keys, one-to-one, many-to-one, many-to-many, QuerySet, filtering,
ordering, pagination, aggregation, transactions, bulk operations.

**Database:** MVP — PostgreSQL, SQLite. Later — MySQL, MariaDB.

**Migrations:** `makemigrations`, `migrate`, `rollback`, `migration status`, schema diff, migration
history, reversible migrations.

**Authentication:** user model, password hashing, login, logout, session authentication,
permissions, groups, password reset foundation.

**Admin:** automatic model registration, CRUD, search, filtering, pagination, authentication,
permissions.

**CLI:**

```text
ferro new | dev | run | makemigrations | migrate | rollback | createsuperuser | shell | routes | db
```

## 5. Developer Experience

```bash
ferro new ecommerce
cd ecommerce
ferro dev
```

```python
from ferro import models

class Product(models.Model):
    name = models.String(max_length=200)
    price = models.Decimal()
    stock = models.Integer(default=0)
    active = models.Boolean(default=True)
```

```python
products = await Product.objects.all()
products = await Product.objects.filter(price__gte=1000, active=True)
product = await Product.objects.create(name="Laptop", price=75000, stock=10)
await Product.objects.filter(id=10).update(stock=20)
await Product.objects.filter(id=10).delete()
```

A Django developer must understand the framework immediately.

## 6. Own ORM Architecture

```text
Python Model API → Model Metadata → Expression Tree → Query Planner
  → Rust Query Builder → SQL Generator → Database Driver → PostgreSQL / SQLite
```

`Product.objects.filter(price__gte=1000, active=True).order_by("-price")` becomes an internal Rust
query representation compiled to:

```sql
SELECT id, name, price, stock, active FROM products
WHERE price >= ? AND active = ?
ORDER BY price DESC
```

Parameters are always bound separately. SQL is never built by string concatenation of values.

## 7. ORM Components

- **Model registry:** Application → Model Registry → Model Metadata → Fields / Relations / Indexes /
  Constraints. Every model registers at startup.
- **MVP fields:** String, Text, Integer, BigInteger, Float, Decimal, Boolean, Date, DateTime, Time,
  UUID, JSON. Later: Binary, Array, Enum, IPAddress, URL, Email, File.
- **Query API (MVP):** all, get, filter, exclude, first, last, count, exists, create, update, delete,
  bulk_create, bulk_update, order_by, limit, offset.
- **Lookups:** exact, contains, icontains, startswith, endswith, gt, gte, lt, lte, in, isnull.
- **Expressions (later):** Q, F, Case, When, Count, Sum, Avg, Min, Max.

## 8. Relationship System

MVP: ForeignKey, OneToOne, ManyToMany. Cross-relation lookups such as
`Product.objects.filter(category__name="Electronics")`. Later: `select_related()`,
`prefetch_related()` to remove N+1 queries.

## 9. Migration Engine (signature feature)

Current Python model schema → previous schema snapshot → schema diff engine → migration operations.

Operations: CreateTable, DropTable, AddColumn, RemoveColumn, AlterColumn, RenameColumn,
CreateIndex, DropIndex, AddForeignKey, RemoveForeignKey, AddConstraint, RemoveConstraint.

Generated names: `0001_create_product`, `0002_add_stock`, `0003_add_category`. A migration history
table is maintained. Migrations are transactional wherever the database supports transactional DDL.

## 10. Transaction API

```python
async with transaction():
    await Order.objects.create(...)
    await Payment.objects.create(...)
```

BEGIN → queries → COMMIT, or ROLLBACK on any failure. Transaction handling lives in Rust.

## 11. Database Layer

ORM → Database Abstraction → Backend Adapter (PostgreSQL, SQLite). Each backend provides: connect,
disconnect, execute, fetch_one, fetch_many, begin, commit, rollback, placeholder_style,
type_mapping, schema_introspection. The underlying Rust DB library is never exposed to users.

## 12. HTTP Layer

Client → Actix HTTP → Router → Middleware → Python route dispatch → Python handler → Rust response
conversion → Actix response. Ferro exposes its own Python API, never Actix APIs.

```python
@app.get("/products/{id}")
async def product(request, id):
    product = await Product.objects.get(id=id)
    return {"id": product.id, "name": product.name}
```

## 13. Python ↔ Rust Bridge

PyO3 is the primary bridge. Async integration uses a Ferro-owned asyncio bridge that cancels
Python tasks on timeout/disconnect (constitution v1.1.0; `pyo3-async-runtimes` lacks
cancel-on-drop).
Actix Request → Rust Request Object → PyO3 → Python Handler → Python Result → Rust Conversion →
Actix Response. Cross-boundary calls stay coarse-grained:
Rust → Python handler → Rust ORM operation → Rust result. Never ping-pong.

## 14. Async Architecture

Tokio + Actix Web multi-worker server. The framework never blocks an Actix worker on database
operations, file I/O, network I/O or long synchronous work. CPU-heavy Python work gets a separate
execution path.

## 15. Security by Default

TLS support, secure headers, CORS, CSRF protection, request size limits, connection limits,
timeouts, rate limiting, password hashing, session security, secure cookies, input validation, SQL
parameter binding, path validation, security logging. Defaults are safe; developers explicitly
weaken security rather than explicitly enabling each protection.

## 16. Built-in Admin

```python
from ferro.admin import admin
admin.register(Product)
admin.register(Category)
```

Generates `/admin` with list, search, filter, create, edit, delete per model. The admin uses the
same ORM and auth system — no separate database abstraction.

## 17. Repository Structure

```text
crates/   core, runtime, http, router, middleware, orm, query, migrations, database, postgres,
          sqlite, auth, security, serialization, python-bridge
python/ferro/   __init__, app, routing, request, response, models, fields, queryset, migrations,
                auth, admin, config
cli/  examples/{hello_world,blog,ecommerce}/  benchmarks/
tests/{rust,python,integration,database,security,benchmarks}/  docs/
Cargo.toml  pyproject.toml
```

## 18. Development Phases

| Phase | Name | Exit criterion |
|---|---|---|
| 0 | Architecture & specification | Public API frozen before advanced features |
| 1 | Rust runtime (Actix integration, lifecycle, config, logging, request/response/router/middleware abstractions, graceful shutdown) | `ferro dev` starts a real server |
| 2 | Python bridge (PyO3, route registration, request/response/exception conversion, async handler bridge) | `@app.get("/hello")` works end to end |
| 3 | ORM foundation (registry, metaclass, field descriptors, schema, QuerySet, expressions, SQL generation) on SQLite | Basic CRUD on SQLite |
| 4 | PostgreSQL backend (pooling, transactions, type mapping, introspection, error mapping) | Full ORM suite passes on SQLite and PostgreSQL |
| 5 | Migration engine (snapshot, diff, generation, runner, history, rollback, SQL compiler) | `makemigrations / migrate / rollback / migration status` |
| 6 | Authentication & security | Login/logout, permissions, CSRF, rate limiting |
| 7 | Admin | CRUD admin over real models |
| 8 | Developer CLI & project generator | `ferro new` scaffolds a working project |
| 9 | Performance engineering | Permanent benchmark suite vs raw Actix baseline |
| 10 | Stability & production hardening | Stress, failure and shutdown tests pass |

## 19. Testing Strategy

Unit tests from day one (Rust: router, ORM, query builder, SQL compiler, migrations, security,
serialization; Python: model API, routes, views, admin, configuration). Integration tests
Python → Rust → ORM → PostgreSQL/SQLite. Every supported database runs the same ORM suite. Stress
tests: concurrency, connection exhaustion, DB latency, large result sets, large bodies.

## 20. Performance Rules (architectural, not optional)

1. No unnecessary Python ↔ Rust crossings.
2. Database query construction lives in Rust.
3. SQL generation lives in Rust.
4. Prepared statements / bound parameters only.
5. No copying large buffers between Python and Rust.
6. Never block async workers.
7. Bounded queues wherever backpressure is required.
8. Measure every major release.

## 21. MVP Definition of Done

An e-commerce application (users, products, categories, orders, payments, authentication, admin,
PostgreSQL, migrations, REST API, transactions) built with only Ferro:

```bash
ferro new ecommerce
ferro dev
ferro makemigrations
ferro migrate
ferro createsuperuser
```

## 22. Out of MVP Scope

GraphQL, WebRTC, message broker, distributed transactions, microservices framework, cluster
orchestration, sharding, advanced event sourcing, built-in task queue, built-in frontend framework,
multi-region deployment, cloud platform.

## 23. Version Roadmap

- **v0.1 Internal prototype:** Actix, PyO3, routing, Python handlers, SQLite, basic ORM.
- **v0.2 ORM preview:** models, QuerySet, relations, transactions, migrations, PostgreSQL.
- **v0.3 Framework preview:** auth, middleware, CLI, admin, error handling, security.
- **v0.4 Performance preview:** benchmark suite, pool/serialization/boundary optimization,
  concurrency testing.
- **v0.5 Release candidate:** docs, examples, migration stability, security audit, API stability,
  production deployment.
- **v1.0:** runs real CRM, ERP, e-commerce, SaaS, dashboard, business-management and REST apps.

## 24. Positioning

Not "FastAPI but written in Rust". Ferro is **a Python application framework with a Rust-native
execution engine and ORM**: Django-class models, ORM, migrations, admin, auth and CLI on top of
Actix Web, Tokio, a native ORM engine, native DB layer, native security and native concurrency.

## 25. First Technical Milestone

```python
from ferro import App

app = App()

@app.get("/")
async def home():
    return {"message": "Hello from Rust"}
```

`ferro dev` → `GET /` → `{"message": "Hello from Rust"}`.

Next milestone: `class User(models.Model)` → `ferro makemigrations` → `ferro migrate` →
`await User.objects.all()`. Together these prove the Rust web runtime and the owned ORM.
