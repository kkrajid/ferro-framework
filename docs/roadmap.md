# Ferro — Spec Kit Feature Roadmap

Each row is one Spec Kit feature (`/speckit-specify` → `/speckit-clarify` → `/speckit-plan` →
`/speckit-tasks` → `/speckit-analyze` → `/speckit-implement`). Features run in order; each depends
on the ones above it. Source sections refer to [product-vision.md](product-vision.md).

| # | Feature | Vision phases / sections | Release | Status |
|---|---|---|---|---|
| 001 | Runtime core & Python bridge: `App`, `@app.get/post/...`, path/query params, JSON request/response, error handling, `ferro dev` / `ferro run` / `ferro routes` | Phase 1–2, §12–14, §25 | v0.1 | Implemented ([specs/001-runtime-core](../specs/001-runtime-core/)) |
| 002 | ORM foundation on SQLite: model registry, field system, QuerySet, lookups, CRUD, SQL compiler | Phase 3, §6–7 | v0.1 | Implemented ([specs/002-orm-foundation](../specs/002-orm-foundation/)) |
| 003 | PostgreSQL backend: connection pooling, type mapping, Unicode-aware text lookups, error mapping, every ORM test run on SQLite and PostgreSQL | Phase 4, §11 | v0.2 | Implemented ([specs/003-postgresql-backend](../specs/003-postgresql-backend/)) |
| 004 | Migration engine: `ferro makemigrations` / `migrate` / `rollback` / `migration status`, schema snapshot + diff, history table, reversible migrations | Phase 5, §9 | v0.2 | Implemented ([specs/004-migration-engine](../specs/004-migration-engine/)) |
| 005 | Relationships, transactions & aggregation: ForeignKey, OneToOne, ManyToMany, cross-relation lookups, `transaction()`, `Count/Sum/Avg/Min/Max`, `Q`/`F` (with migration support for foreign keys) | §7–8, §10 | v0.2 | Implemented ([specs/005-relations-transactions](../specs/005-relations-transactions/)) |
| 006 | Middleware & security defaults: middleware API, secure headers, CORS, CSRF, limits, timeouts, rate limiting | §15 | v0.3 | Implemented ([specs/006-middleware-security](../specs/006-middleware-security/)) |
| 007 | Authentication: user model, password hashing, sessions, login/logout, permissions, groups | Phase 6 | v0.3 | Implemented ([specs/007-authentication](../specs/007-authentication/)) |
| 008 | Admin: registration, CRUD, search, filter, pagination, permissions | Phase 7, §16 | v0.3 | Implemented ([specs/008-admin](../specs/008-admin/)) |
| 009 | CLI & project generator: `ferro new`, `shell`, `db`, `createsuperuser`, templates | Phase 8 | v0.3 | Implemented ([specs/009-cli-generator](../specs/009-cli-generator/)) |
| 010 | Performance suite: benchmarks vs raw Actix baseline, boundary/serialization optimization | Phase 9, §20 | v0.4 | Implemented ([specs/010-performance](../specs/010-performance/)) |
| 011 | Production hardening: stress/failure tests, metrics, request IDs, health/readiness, graceful shutdown | Phase 10 | v0.5 | Implemented ([specs/011-production-hardening](../specs/011-production-hardening/)) |
| 012 | MVP acceptance: e-commerce example app proving the Definition of Done | §21 | v0.5 | Implemented ([specs/012-mvp-acceptance](../specs/012-mvp-acceptance/), [examples/ecommerce](../examples/ecommerce/)) |
| 013 | ORM completion: `select_for_update()`, custom `through=` link models, model inheritance (abstract bases) | §7–8 | v0.5 | Implemented ([specs/013-orm-completion](../specs/013-orm-completion/)) |
| 014 | TLS and HTTP/2 in the engine (rustls), certificate settings | §12, §15 | v0.5 | Implemented ([specs/014-tls-http2](../specs/014-tls-http2/)) |
| 015 | Shared state across worker processes (metrics, rate limits) and a cache API (`ferro.cache`: memory, database) | §20 | v0.5 | Implemented ([specs/015-shared-state-cache](../specs/015-shared-state-cache/)) |
| 016 | Packaging and release: wheels, sdist, versioning, changelog, release workflow, install test | §23 v0.5 | v0.5 | Implemented ([specs/016-packaging-release](../specs/016-packaging-release/)) |
| 017 | v0.5 release candidate: security audit, API freeze and stability policy, production deployment guide | §23 v0.5 | v0.5 | Implemented ([specs/017-release-candidate](../specs/017-release-candidate/)) |
| 018 | Real-time: WebSockets, streaming responses and server-sent events, broadcast (memory, PostgreSQL LISTEN/NOTIFY) | §12, §14, §23 v1.0 (dashboards, SaaS) | next | Implemented ([specs/018-websockets-streaming](../specs/018-websockets-streaming/)) |
| 019 | Pydantic integration: model-annotated request bodies, response models from return annotations, models in every JSON output | §5, §12, §23 v1.0 (REST apps) | next | Implemented ([specs/019-pydantic-integration](../specs/019-pydantic-integration/)) |
| 020 | DB latency: debug-build warning, BEGIN pipelined with the first statement, checkout benchmark and profile | §20 | next | Implemented ([specs/020-db-latency](../specs/020-db-latency/)) |
| 021 | Database completeness: isolation levels, connection lifetimes, `RunSQL`/`RunPython`, extensions, `iterator()`, sequence reset | §9, §11 | v0.6 | Implemented ([specs/021-database-completeness](../specs/021-database-completeness/)) |
| 022 | ORM completeness: more lookups, JSON and date queries, expressions, subqueries, raw SQL | §7–8 | v0.6 | Implemented ([specs/022-orm-completeness](../specs/022-orm-completeness/)) |
| 023 | Site security: allowed hosts, HTTPS redirect, HSTS options, login throttling | §15 | v0.6 | Implemented ([specs/023-site-security](../specs/023-site-security/)) |
| 024 | Signals and hooks: model, request and login signals, `on_commit`, startup/shutdown | §12 | v0.6 | Implemented ([specs/024-signals-and-hooks](../specs/024-signals-and-hooks/)) |
| 025 | Requests and responses at FastAPI/Flask level: uploads, `Form`/`Header`/`Cookie`/`Depends`, background tasks, files, static, OpenAPI, templates | §12–14 | v0.6 | Implemented ([specs/025-requests-responses](../specs/025-requests-responses/)) |
| 026 | APIs, uploads and jobs: dependency overrides, OpenAPI security, uploads on disk, durable jobs, benchmark workflow | §12, §20 | v0.6 | Implemented ([specs/026-apis-uploads-jobs](../specs/026-apis-uploads-jobs/)) |

Reordered on 2026-10-10 at the user's request: PostgreSQL and migrations come before relations,
so migrations are built and tested on both databases from the start (constitution VII).
Specs 001–002 written earlier refer to the old numbers (004 = PostgreSQL, 005 = migrations,
003 = relations).
