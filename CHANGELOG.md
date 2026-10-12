# Changelog

All notable changes to Ferro. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/) with the pre-1.0 rules in
[docs/stability.md](docs/stability.md).

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
