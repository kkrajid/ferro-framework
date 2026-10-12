# Security review — Ferro 0.5.0rc1

**Date**: 2026-10-11 · **Scope**: the whole repository (engine, ORM, bridge, Python package,
admin, CLI, shared state) · **Method**: code review of every component against the threats in
[product-vision.md](product-vision.md) §15, each finding confirmed by reading the code path, then
fixed with a regression test (Feature 017). No external penetration test was done.

**Result**: no critical or high findings. Ten medium/low findings; eight fixed, two accepted
with documentation.

## Findings

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | Medium | `X-Forwarded-For` read from its first header line only: behind a proxy that *adds* a line (HAProxy `option forwardfor`), a client-supplied line decided the client address — bypassing per-IP rate limits and `metrics_allow` | **Fixed**: all lines are joined in order before the rightmost-untrusted scan (`crates/http/src/service.rs` `all_values`). Test: `crates/http/tests/security_review.rs` |
| 2 | Medium | `CORS(allow_origins=["*"])` made every origin "trusted" for the cross-origin (CSRF) check, including admin form posts from a sibling subdomain carrying the session cookie | **Fixed**: a wildcard only lets cookie-less requests through; listed origins stay trusted (`Policy::csrf_allows`, `CorsConfig::lists`). Test: `wildcard_cors_never_trusts_requests_with_cookies` |
| 3 | Medium (DoS) | Anonymous requests stored session rows (`SessionData.pop` marked the session changed even for a missing key; admin pages pop a flash message), and expired rows were never deleted | **Fixed**: `pop` of a missing key changes nothing; empty anonymous sessions are not stored; expired sessions are deleted at every login. Tests: `tests/python/auth/test_security_review.py` |
| 4 | Medium/Low | Rate limits keyed on the full IPv6 address (rotation inside a /64 gave fresh budgets) and bucket memory unbounded for long periods | **Fixed**: IPv6 clients are keyed by /64 (IPv4-mapped by their IPv4); at most 4,096 buckets per shard (65,536), least recently used evicted. Shared table: fixed size by design. Tests: `ipv6_clients_share_a_budget_per_64`, `bucket_memory_is_capped` |
| 5 | Low | Trailing-slash redirect refused `//host` but not `/\host`, which browsers treat alike | **Fixed**: both refused (404). Test: `backslash_paths_never_redirect_off_site` |
| 6 | Low | Sessions survived a password change and had no absolute lifetime (sliding 14 days forever) | **Fixed**: a session stores a digest of the password hash at login and its login time; a changed password or a login older than 30 days signs out. Tests: `test_changing_the_password_ends_other_sessions`, `test_logins_have_an_absolute_lifetime` |
| 7 | Low | Password-hashing queue unbounded; hashes still ran for requests already cancelled | **Fixed**: 32 queued hashes per pool thread, beyond that `503 Retry-After: 1`; queued jobs of finished/cancelled requests are skipped. Test: `test_password_hashing_floods_get_503` |
| 8 | Low | Registering `User` in the admin showed the password hash in a text input and stored a typed password unhashed; staff with `change_auth_user` could make themselves superuser | **Fixed**: the password is a write-only password input hashed with `set_password` (validated); `is_superuser`, `groups` and `user_permissions` are read-only for non-superusers. Tests: `tests/python/admin/test_user_admin.py` |
| 9 | Low | PostgreSQL `sslmode` defaults to `prefer`, which an active network attacker can downgrade | **Accepted, documented**: the deployment guide uses `sslmode=require` for remote databases; changing the default would break local and private-network setups |
| 10 | Info | `_ferro.Server.start(listener)` took any integer as a socket; the shared-state file and `.env` were created with default permissions | **Fixed**: descriptors are range-checked (POSIX: > 2); the shared state lives in a `mkdtemp` directory (0700) and its file is created 0600; `ferro new` writes `.env` 0600 (POSIX). `start(listener)` stays a private API |

Operational note (finding 1, related): with a local reverse proxy but no `trusted_proxies`,
every client appears as `127.0.0.1` — sharing one rate-limit budget and passing the default
`metrics_allow`. The deployment guide and `deploy/` files set `FERRO_TRUSTED_PROXIES`, and the
nginx example blocks `/metrics`.

## Checked and found sound

- **SQL injection**: identifiers always quoted by the dialect; every value (including
  LIMIT/OFFSET) bound; LIKE and GLOB patterns escaped; no raw-SQL API; DDL, migration SQL and
  savepoint names safe; admin search/filter/order only use configured or known fields.
- **HTML injection (admin)**: every value escaped by the tag builder; links built from quoted keys
  and table names; strict Content-Security-Policy with a style hash.
- **Admin authorization**: staff check and per-model permissions on every view; delete is a GET
  confirmation and a POST action; read-only fields enforced on the server; logout is POST-only;
  the login `next` must start with the admin prefix and contain no `//` or `\`.
- **CSRF**: safe methods; `Sec-Fetch-Site` same-origin/none; `null`/malformed `Origin` refused;
  cross-site metadata without `Origin` refused; host comparison normalized (now also from the
  HTTP/2 authority).
- **CORS**: `*` with credentials refused (Python and Rust); `Vary: Origin` set.
- **Sessions**: 256-bit random keys, only their SHA-256 stored, rotated on login, deleted on
  logout, unknown cookies cleared; `HttpOnly`, `SameSite=Lax`, `Secure` outside debug.
- **Passwords**: Argon2id (19 MiB, t=2, p=1), random salts, constant-time verification, a dummy
  hash for unknown/inactive users (no account enumeration by timing).
- **Header injection**: header names/values validated in Python and Rust; request ids restricted
  to a safe alphabet.
- **Limits**: body size (declared and streamed), one deadline for body and handler, header and
  TLS-handshake timeouts, connection cap, JSON nesting limits, Python's integer-digit limit, no
  automatic decompression.
- **Information leaks**: tracebacks only in debug; database outages answered 503 without detail;
  readiness lists check names only; metric labels bounded by the route table.
- **TLS**: rustls, TLS 1.2/1.3 only, safe defaults; PostgreSQL TLS verifies certificates; key
  material never logged (tested).
- **Unsafe Rust**: `ferro-shared` (alignment, size and layout checked, atomics only) and listener
  adoption are sound under their documented conditions.
- **Deserialization and code execution**: no pickle or YAML; `eval` only in `ferro shell`; the
  cache stores JSON; migration loader only loads `NNNN_name.py` from the configured directory;
  migration names validated; `ferro new` validates project names.
