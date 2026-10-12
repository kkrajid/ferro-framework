# Research: Authentication

**Feature**: [spec.md](spec.md) | **Date**: 2026-10-11

## R1. Password hashing

- **Decision**: Argon2id via the RustCrypto `argon2` crate (pure Rust, PHC strings,
  constant-time verify) in `ferro-core`'s new `passwords` module; parameters m=19456 KiB, t=2,
  p=1 (OWASP 2023). The bridge runs hash/verify on a dedicated pool of `min(4, cpus)` threads and
  resolves an asyncio future through the existing delivery path, so the event loop and HTTP
  workers never block (SC-002).
- `needs_rehash(hash)` compares the stored parameters with the current ones; `authenticate`
  rehashes after a successful verification.
- Unknown formats (not `$argon2id$`) verify as false.
- **Alternatives rejected**: PBKDF2 (weaker against GPUs), bcrypt (72-byte limit), hashing in
  Python (`hashlib.scrypt` blocks the loop and is not constant-time to compare without care).

## R2. Timing equalization

`authenticate` verifies against a fixed dummy hash when the user does not exist or is inactive,
so both paths cost one Argon2 verification (SC-004).

## R3. Sessions

- Model `Session(key_digest: String(64, unique), user: FK User CASCADE null, data: JSON,
  expires: DateTime, index)`; table `auth_session`.
- Key: `secrets.token_urlsafe(32)` (256 bits); stored as SHA-256 hex. Cookie `sessionid`.
- Middleware: read cookie → `Session.objects.select_related("user").filter(key_digest=...,
  expires__gt=now).first()` (one query, SC-003) → `request.user`, `request.session`. After the
  handler: if the session data changed or login/logout happened, save/delete and set/clear the
  cookie; sliding expiry refreshes `expires` at most once per hour to avoid a write per request.
- Login rotates: delete the old row, create a new key (keeping data).

## R4. Request integration

`request.user` / `request.session` are native getters reading `request.state` (set by the auth
middleware); without the middleware they raise a clear error. `app.add_auth()` registers the
middleware (and requires the database).

## R5. Permissions and groups

- `Permission(codename unique, name)`, `Group(name unique, permissions M2M Permission)`,
  `User(groups M2M Group, user_permissions M2M Permission)`.
- `has_perm(code)`: active superuser → True; inactive → False; otherwise one EXISTS query
  `Permission.objects.filter(Q(user=user) | Q(group__user=user), codename=code).exists()`, cached
  per user instance for the request.
- `create_permissions()` inserts missing `<action>_<table>` rows with `bulk_create(...,
  ignore_conflicts=True)`; `ferro migrate` calls it when `ferro.auth` is in use.

## R6. Decorators

`login_required`, `permission_required(*codes)`, `staff_required` wrap handlers; they find the
request among the handler's parameters (the routing layer injects `request` when declared) —
decorated handlers must declare a `request` parameter, checked at decoration time.

## R7. createsuperuser

`ferro createsuperuser [TARGET] --username U [--email E] [--database-url URL]`; password from
`FERRO_SUPERUSER_PASSWORD` or an interactive prompt (twice, hidden); validation as in US1.
