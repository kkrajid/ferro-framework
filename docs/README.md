# Ferro documentation

Ferro is a Python web framework with a Rust engine: you write models, routes and handlers in
Python; HTTP, routing, limits, security checks, JSON and SQL run in Rust. It comes with its own
ORM and migrations (SQLite and PostgreSQL), authentication, an admin, a cache, and a production
server — no Gunicorn, Uvicorn or Daphne needed.

## Start here

| If you want to… | Read |
|---|---|
| install Ferro | [Installation](guide/01-installation.md) |
| build your first app step by step | [Tutorial](guide/02-tutorial.md) |
| see everything in one page | [Quick tour](getting-started.md) |
| look at a complete application | [examples/ecommerce](../examples/ecommerce/) |

## User guide

1. [Installation](guide/01-installation.md)
2. [Tutorial: a notes API with users and an admin](guide/02-tutorial.md)
3. [Routing, requests and responses](guide/03-routing-requests-responses.md) — routes,
   parameters, bodies, forms, cookies, errors, middleware
4. [Models and queries](guide/04-models-and-queries.md) — fields, the QuerySet API, relations,
   aggregation, transactions, locking, inheritance
5. [Migrations](guide/05-migrations.md)
6. [Authentication and permissions](guide/06-authentication.md)
7. [The admin](guide/07-admin.md)
8. [Caching](guide/08-cache.md)
9. [Security](guide/09-security.md) — CSRF, CORS, rate limits, headers, cookies, proxies, HTTPS
10. [Testing](guide/10-testing.md)
11. [Logging, health checks and metrics](guide/11-logging-monitoring.md)
12. [Real-time: WebSockets, server-sent events and streaming](guide/16-realtime.md) —
    chat, notifications, progress, large downloads, broadcast across processes
13. [Request and response models with Pydantic](guide/17-pydantic.md) — validated bodies,
    response models from ORM objects, 422 errors

## Running in production

- [Deployment guide](deployment.md) — from an empty server to HTTPS, Docker, Kubernetes,
  platforms as a service; ready-made files in [`deploy/`](../deploy/)
- [Performance and scaling](guide/12-performance-scaling.md) — workers, processes, machines,
  database connections, caching, load testing
- [Troubleshooting and FAQ](guide/13-troubleshooting.md)

## Reference

- [Settings reference](guide/14-settings-reference.md) — every setting, option, environment
  variable and default
- [Command-line reference](guide/15-cli-reference.md) — every `ferro` command and option
- [Public API and stability policy](stability.md)
- [Security review](security-audit.md) and [security policy](../SECURITY.md)
- [Changelog](../CHANGELOG.md) · [Roadmap](roadmap.md) · [Releasing](releasing.md)

## How Ferro is built

The framework is developed spec-first: every feature has a specification, plan, contracts and
task list in [`specs/`](../specs/), and the principles are in
[`.specify/memory/constitution.md`](../.specify/memory/constitution.md). The product plan is
[product-vision.md](product-vision.md).
