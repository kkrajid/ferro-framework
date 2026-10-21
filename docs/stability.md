# Public API and stability policy

From 0.5.0 the API below is **frozen for the 0.5 series**: 0.5.x releases only fix bugs and add
things; nothing listed here changes or disappears. Anything not listed is internal and may change
in any release.

## Versioning

- `0.MINOR.PATCH` until 1.0. A minor release (0.6) may change the public API, but only after a
  deprecation (below). A patch release (0.5.1) never changes the public API.
- Release candidates (`0.5.0rc1`) are feature-complete; only fixes follow until the final release.
- After 1.0: [Semantic Versioning](https://semver.org/) — breaking changes only in major
  releases.

## Deprecation

A public name or behaviour that will change first emits `DeprecationWarning` (or a log warning
for settings and command-line options) for at least one minor release, with the replacement in
the message and in the [changelog](../CHANGELOG.md). Security fixes are the only exception: they
may change behaviour in a patch release, documented in the changelog under "Security".

## What is public

**Python packages** — the names in each module's `__all__`:

| Module | Public names |
|---|---|
| `ferro` | `App`, `Request`, `Response`, `JSONResponse`, `HTMLResponse`, `PlainTextResponse`, `RedirectResponse`, `FileResponse`, `StreamingResponse`, `EventStream`, `ServerSentEvent`, `UploadFile`, `FormData`, `Depends`, `Form`, `Header`, `Cookie`, `BackgroundTasks`, `WebSocket`, `WebSocketDisconnect`, `HTTPException`, `ValidationError`, `ConfigurationError`, `CORS`, `__version__` |
| `ferro.templating` | `Templates` |
| `ferro.models` | `Model`, `Field` and the field classes (`String`, `Text`, `Integer`, `BigInteger`, `Float`, `Decimal`, `Boolean`, `Date`, `DateTime`, `Time`, `UUID`, `JSON`), relations (`ForeignKey`, `OneToOne`, `ManyToMany`, `CASCADE`, `SET_NULL`, `PROTECT`, `DO_NOTHING`), `QuerySet`, `Q`, `F`, aggregates (`Count`, `Sum`, `Avg`, `Min`, `Max`), expressions (`Value`, `Func`, `Lower`, `Upper`, `Trim`, `Length`, `Abs`, `Concat`, `Coalesce`, `Greatest`, `Least`, `Now`, `Case`, `When`, `Subquery`, `Exists`, `OuterRef`), `prefetch_related_objects`, exceptions (`FieldError`, `DatabaseError`, `IntegrityError`, `ProtectedError`, `OperationalError`, `DatabaseUnavailableError`, `TransactionError`, `ObjectDoesNotExist`, `MultipleObjectsReturned`, `RelatedNotLoaded`) |
| `ferro.db` | `connect`, `disconnect`, `is_connected`, `create_tables`, `transaction`, `request_transaction`, `on_commit`, `execute`, `fetch`, `reset_sequences`, `DatabaseUnavailableError`, `TransactionError` |
| `ferro.jobs` | `task`, `Task`, `enqueue`, `retry`, `run_worker`, `Job` (the `ferro_job` table) |
| `ferro.security` | `CORS`, security scheme dependencies (`SecurityScheme`, `HTTPBearer`, `HTTPBasic`, `APIKeyHeader`, `APIKeyQuery`, `APIKeyCookie`) |
| `ferro.auth` | `User`, `Group`, `Permission`, `Session`, `AnonymousUser`, `authenticate`, `login`, `logout`, `login_required`, `staff_required`, `permission_required`, `auth_middleware`, `make_password`, `validate_password`, `PasswordError`, `create_permissions`, `LoginThrottled` |
| `ferro.admin` | `admin`, `AdminSite`, `ModelAdmin` |
| `ferro.signals` | `Signal`, `receiver` and the built-in signals (`pre_save`, `post_save`, `pre_delete`, `post_delete`, `m2m_changed`, `request_started`, `request_finished`, `got_request_exception`, `user_logged_in`, `user_logged_out`, `user_login_failed`, `post_migrate`) |
| `ferro.cache` | `cache`, `configure`, `BaseCache`, `MemoryCache`, `DatabaseCache`, `DEFAULT_TIMEOUT` |
| `ferro.migrations` | `Migration`, `MigrationError`, `ops` (operations used in migration files) |
| `ferro.websockets` | `WebSocket`, `WebSocketDisconnect` |
| `ferro.broadcast` | `broadcast`, `configure`, `Broadcast`, `Subscription`, `MAX_MESSAGE_BYTES` |
| `ferro.testing` | `TestClient`, `TestResponse`, `TestStreamResponse`, `TestWebSocket`, `WebSocketRejected`, `WebSocketDisconnect`, `connect_websocket` |

Public behaviour includes the documented methods and attributes of these classes (for example
every `QuerySet` method in the [guide](getting-started.md), `Request` attributes, `Model`
methods), and the documented semantics (lookups, `on_delete`, transactions, error envelopes).
With Pydantic installed, the [Pydantic integration](guide/17-pydantic.md) is public behaviour
too: model-annotated body parameters, return-annotation response models, models accepted
wherever JSON is written, and the `ferro-framework[pydantic]` extra.

**Command line**: `ferro new`, `dev`, `run`, `routes`, `shell`, `db create-tables`, `db shell`,
`makemigrations`, `migrate`, `rollback`, `migration status`, `createsuperuser`, `jobs worker`, their documented
options and the exit codes (0, 1, 2, 3).

**Settings**: every setting in the guide's settings table, with its option, environment variable
(`FERRO_*`), `App(...)` argument, default and meaning.

**HTTP behaviour**: probe paths and bodies (`/healthz`, `/readyz`), the error envelope
(`{"error": {"status", "message", "request_id"}}`), `x-request-id`, security headers set by
default, metric names and labels, the migration file format (existing migration files keep
working).

## What is not public

- `ferro._ferro` (the native module) and every name starting with `_`.
- Rust crates (`ferro-core`, `ferro-http`, ...): they are not published and have no stability
  promise; Python is the only supported interface.
- The internal `_worker` command, shared-state files and the native settings `shared_state`
  and `metrics_dir`.
- Log message texts (fields such as `request_id` and the JSON log keys are public).
- Benchmark numbers.

## Supported versions

| Ferro | Python | Fixes |
|---|---|---|
| 0.5.x | 3.12, 3.13 (abi3 wheels also load on newer CPython) | current |

Security issues: report privately to the maintainers (see the repository's security policy);
fixes are released for the current minor series.
