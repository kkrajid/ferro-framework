# Changelog

All notable changes to Ferro. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/) with the pre-1.0 rules in
[docs/stability.md](docs/stability.md).

## [Unreleased]

## [0.6.0rc1] — 2026-10-21

First release candidate of 0.6: APIs, dependencies, uploads and jobs (Feature 026), requests
and responses at FastAPI/Flask level (025), signals (024), site security (023), ORM (022) and
database (021) completeness, and the emSign CA security fix.

### Security

- **eMudhra/emSign CA key compromise** (RUSTSEC-2026-0381): PostgreSQL TLS no longer trusts
  "emSign ECC Root CA - G3", whose intermediate keys are public (`webpki-roots` 1.0.10).
  Ferro 0.5.0rc2 and earlier trust it when connecting with `sslmode=require`/`verify-full`:
  upgrade. A test keeps the root out. Development and CI tools now require
  `certifi>=2026.10.19`, `uv>=0.13.4` and `pip>=26.2.2`.

Follow-up review of Features 018–020 ([docs/security-audit.md](docs/security-audit.md)):

- A PostgreSQL connection lost between two statements of a transaction no longer lets the
  rest of the transaction autocommit on a new connection; it fails instead.
- Unread WebSocket messages are limited to 1 MiB per connection (not only 32 messages);
  writes to a client that stopped reading time out after 60 s.
- WebSocket sessions of a login invalidated by a password change carry no session data.
- Response-annotation errors no longer log the returned values; server-sent event fields
  are checked when sent.

### Added

- APIs, dependencies, uploads and jobs (Feature 026):
  - `app.dependency_overrides` and `app.override_dependency()` for tests;
    `ferro.db.request_transaction` (one transaction per request, as a dependency);
  - security schemes in `ferro.security`: `HTTPBearer`, `HTTPBasic`, `APIKeyHeader`,
    `APIKeyQuery`, `APIKeyCookie`, listed in the OpenAPI document with the session cookie of
    `login_required`;
  - route options `status_code`, `summary`, `description`, `responses`, `deprecated`,
    `operation_id`; `add_openapi(servers=...)`;
  - request bodies over 1 MiB are kept in a temporary file (memory-mapped) instead of memory;
    `UploadFile.chunks()`, a seekable `UploadFile.file` and `save()` read in pieces;
  - `ferro.jobs`: durable jobs in the database (`@jobs.task`, transactional `enqueue`,
    retries with backoff, timeouts, visibility timeout) and `ferro jobs worker`;
  - `.github/workflows/benchmark.yml`: the FastAPI/Flask comparison on a GitHub runner from a
    release build with pinned versions (uvloop), with hardware and p50/p90/p99/p99.9.
- Requests and responses at FastAPI/Flask level (Feature 025):
  - file uploads: `UploadFile` parameters, `multipart/form-data` in `request.form()`,
    per-route `max_body_size`, `TestClient(files=...)`;
  - `Form`, `Header` and `Cookie` parameters; `Depends` (with generator cleanup and per-request
    caching); `BackgroundTasks`;
  - `HTMLResponse`, `PlainTextResponse`, `RedirectResponse`, `FileResponse` (ETag, 304, ranges,
    streamed with `Content-Length`);
  - `app.static()`, `{name:path}` route segments, `app.url_for()`;
  - `app.add_openapi()`: OpenAPI 3.1 document and Swagger UI;
  - `ferro.templating.Templates` (Jinja2, `ferro-framework[templates]`).
- Signals and hooks (Feature 024): `ferro.signals` (`Signal`, `receiver`, `pre_save`,
  `post_save`, `pre_delete`, `post_delete`, `m2m_changed`, `request_started`,
  `request_finished`, `got_request_exception`, `user_logged_in`, `user_logged_out`,
  `user_login_failed`, `post_migrate`), `db.on_commit()`, `@app.on_startup` and
  `@app.on_shutdown`.
- Site security settings (Feature 023), from Django's security guide: `allowed_hosts`
  (other `Host` headers get 400; `ferro run` warns when it is unset), `https_redirect` (308
  to HTTPS behind a proxy), `hsts_include_subdomains` and `hsts_preload`.
- Failed logins are throttled: `authenticate()` raises `LoginThrottled` (429) after 5 failures
  per username (and client address, with the new `request` argument) for 15 minutes;
  `app.add_auth(max_login_attempts=..., login_lockout=...)`.
- ORM completeness (Feature 022), from a comparison with Django's "Making queries":
  - Lookups `istartswith`, `iendswith`, `range`, `regex`, `iregex`; `__in` with a queryset.
  - Date transforms: `__year`, `__quarter`, `__month`, `__day`, `__week_day`, `__hour`,
    `__minute`, `__second`, `__date` (UTC), combinable with lookups.
  - JSON queries: key paths (`profile__address__city`), `has_key`, `has_keys`,
    `has_any_keys`, `contains` (containment, also on SQLite).
  - Expressions in `annotate()`: `Value`, `Lower`, `Upper`, `Trim`, `Length`, `Abs`,
    `Concat`, `Coalesce`, `Greatest`, `Least`, `Now`, `Case`/`When`, `Subquery`, `Exists`,
    `OuterRef`; `F() % n`; `Q ^ Q`.
  - `QuerySet.distinct()`, `none()`, `get_or_create()`, `update_or_create()`, `in_bulk()`,
    `explain()`, `raw()`; `db.execute()` and `db.fetch()` for parameterized SQL.
  - Reverse foreign-key managers: `add()`, `remove()`, `clear()`, `set()`.
- Database completeness (Feature 021), from a comparison with Django's database reference:
  - `transaction(isolation="read committed" | "repeatable read" | "serializable")`.
  - `QuerySet.iterator(chunk_size=1000)`: async iteration in chunks (keyset pagination on the
    primary key when unordered), no server-side cursor.
  - Migration operations `ops.RunSQL`, `ops.RunPython` (data migrations, in the migration's
    transaction), `ops.CreateExtension` / `ops.DropExtension`.
  - PostgreSQL URL parameters `max_lifetime` and `max_idle`; SQLite URL parameter `timeout`.
  - `ferro db sequence-reset` / `db.reset_sequences()`.
- `ferro run` and `ferro dev` warn when the native module is an unoptimized debug build
  (`maturin develop`), which is 3–5 times slower; the performance guide explains how to measure.
- `tests/benchmarks/run_020.py`: the e-commerce checkout under load on PostgreSQL.

### Changed

- PostgreSQL transactions send `BEGIN` together with their first statement (one round trip
  less), and a transaction that runs no statement sends nothing.
- PostgreSQL serialization failures and deadlocks raise `DatabaseUnavailableError` (a subclass
  of `OperationalError`, as before): retry the transaction.
- PostgreSQL: an index on a `String`/`Text` field also gets a `text_pattern_ops` index, so
  `startswith` lookups can use an index under non-C collations.

### Fixed

- `ferro run`, `--processes` and `ferro dev` could ignore a stop request (SIGTERM, Ctrl+Break)
  that arrived while they were logging — about one start in four on Windows when stopped
  right after the banner: the stop signal was an ordinary exception that logging swallowed.
- `TestClient.websocket_connect(headers={"host": ...})` sent two `Host` headers.
- PostgreSQL: inserting a row with an explicit primary key no longer leaves the key sequence
  behind (the next `create()` failed with `IntegrityError`).
- The e-commerce example inserts an order once, with its total.

## [0.5.0rc2] — 2026-10-20

Second release candidate; the first one published to PyPI (0.5.0rc1 was never uploaded, so
this candidate also adds the features below).

### Changed

- The distribution is now called **`ferro-framework`** on PyPI (`pip install
  ferro-framework`); the name `ferro` belongs to an unrelated project. The import name
  (`import ferro`) and the `ferro` command are unchanged. Projects created by `ferro new`
  depend on `ferro-framework`; existing projects should change `"ferro"` to
  `"ferro-framework"` in their dependencies.

### Added

- **Real-time** (Feature 018, [guide](docs/guide/16-realtime.md)): WebSocket endpoints
  (`@app.websocket`, `ferro.WebSocket`, `WebSocketDisconnect`) with the handshake checked for
  origin and rate limits, `ws.user`/`ws.session` with `app.add_auth()`, pings, back-pressure
  and close code 1001 on shutdown; `StreamingResponse` and server-sent events
  (`EventStream`, `ServerSentEvent`) sent chunk by chunk, over HTTP/1.1 and HTTP/2 (a body
  that cannot finish is aborted, never ended as if complete);
  `ferro.broadcast` publish/subscribe in one process (`memory`) or across processes and
  machines with PostgreSQL `LISTEN`/`NOTIFY` (`broadcast="database"`, `FERRO_BROADCAST`);
  `TestClient.websocket_connect()` and `TestClient.stream()`; metrics
  `ferro_websocket_connections` and `ferro_http_streams_open`.
- `deploy/nginx.conf` passes WebSocket upgrades; `deploy/Caddyfile` flushes streamed
  responses at once.
- **Pydantic integration** (Feature 019, [guide](docs/guide/17-pydantic.md)): a handler
  parameter annotated with a Pydantic model receives the validated JSON or form body (422
  with every problem, 400 for malformed JSON, 415 for other content types); a return
  annotation using Pydantic models converts results — Ferro model instances included — and
  sends only the declared fields; Pydantic models are accepted wherever Ferro writes JSON.
  Optional extra `ferro-framework[pydantic]`.
- Related managers (`category.products`, many-to-many) can be iterated after
  `prefetch_related`/`fetch_related` (iterating never queries; `RelatedNotLoaded` otherwise).
- `TestClient` requests take `content=` for raw bodies; `ferro routes` lists WebSocket routes
  as `WEBSOCKET`.

### Fixed

- `ferro dev` could keep serving the old code after an edit that kept the file size within the
  same second (Python reused the stale `.pyc`); the reloader now drops the changed files'
  bytecode before restarting.
- With `--processes N`, a client's first requests arriving in several workers at once could
  each claim an entry in the shared rate table and get one extra request; they now share one.
- `ferro_build_info` reports the version as `ferro --version` does (`0.5.0rc2`, not
  `0.5.0-rc.2`).
- The release workflow file did not parse; GitHub Actions moved to Node 24 versions.

## [0.5.0rc1] — 2026-10-11

First release candidate; the first version published as wheels.

### Added

- **ORM completion** (Feature 013): `select_for_update(nowait=, skip_locked=)`; many-to-many
  relations through your own link model (`through=`, `through_fields=`, `through_defaults=`);
  abstract base models (`Meta.abstract = True`, `%(class)s` in `related_name`).
- **TLS and HTTP/2** (Feature 014): `--tls-cert` / `--tls-key` serve HTTPS (TLS 1.2/1.3) with
  HTTP/2 offered through ALPN; certificate problems are reported before the server listens.
- **Shared state** (Feature 015): with `--processes N`, rate limits are enforced across workers
  (shared-memory table) and `/metrics` reports totals over all workers; `ferro.cache` with
  `memory` and `database` backends.
- **Packaging** (Feature 016): wheels for Linux (x86_64, aarch64, musl), macOS (x86_64, arm64)
  and Windows (x64), one per platform for Python 3.12+ (abi3); a source distribution; a release
  workflow with an install test and PyPI trusted publishing.
- **Release candidate** (Feature 017): security review with fixes, the public API and stability
  policy ([docs/stability.md](docs/stability.md)), and the production deployment guide
  ([docs/deployment.md](docs/deployment.md)).

### Security

Fixes from the release review ([docs/security-audit.md](docs/security-audit.md)); some change
behaviour:

- All `X-Forwarded-For` lines are considered, so a client cannot choose its address behind a
  proxy that appends its own line.
- `CORS(allow_origins=["*"])` no longer exempts requests **with cookies** from the cross-origin
  check; list the origins for cookie-authenticated cross-origin posts.
- Rate limits key IPv6 clients by /64 and cap their memory.
- Sessions end when the user's password changes and 30 days after login; anonymous visitors no
  longer create stored sessions; expired sessions are deleted at login. After a user changes
  their own password, call `login(request, user)` again to keep them signed in.
- Trailing-slash redirects refuse `/\host` paths.
- Password hashing answers 503 when 32 hashes per thread are already waiting.
- The admin edits user passwords through `set_password` (never shown) and lets only superusers
  change `is_superuser`, groups and permissions.
- `ferro new` writes `.env` readable by its owner only (POSIX).

### Earlier work (0.1 development line, not published)

- Runtime core on a Rust engine (001), Ferro's ORM on SQLite (002) and PostgreSQL (003),
  migrations (004), relations, transactions and aggregation (005), middleware and security
  defaults (006), authentication (007), admin (008), CLI and project generator (009),
  multi-process serving and benchmarks (010), production hardening (011), MVP acceptance shop
  (012).
