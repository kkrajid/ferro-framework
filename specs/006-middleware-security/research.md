# Research: Middleware & Security Defaults

**Feature**: [spec.md](spec.md) | **Date**: 2026-10-11

## R1. Where each protection runs

- **Decision**: CSRF (origin check), CORS, security headers, cookies parsing helpers and rate
  limiting are implemented in Rust: policy logic in `ferro-core` (`security` module, engine
  independent, unit-tested), applied by `ferro-http`'s service before dispatch / after the
  response. Python middleware wraps endpoints on the Python side.
- **Rationale**: constitution I/V/VI — refusals must not cost Python time (SC-003), and the logic
  is testable without a server. Python middleware is the extension point users expect.

## R2. Python middleware composition

- `@app.middleware` appends to `app._middleware`. When the server starts, every route endpoint is
  wrapped once: `wrapped = mw1(request, call_next=mw2(..., call_next=endpoint))`. No middleware →
  endpoints unchanged (zero cost, SC-002).
- `call_next(request)` runs the inner chain and converts the handler result to a `Response`
  (dict/list → JSON, str → text, bytes → octet-stream, None → 204), with `Response.headers` a
  mutable list of pairs. Calling it twice raises `RuntimeError`. A middleware returning something
  that is not a `Response` → 500 naming it.
- Handler exceptions propagate through `call_next` (middleware may catch them); the bridge's error
  conversion stays the single place that builds error envelopes.
- `request.state`: a lazily created `types.SimpleNamespace` held by the native request object.

## R3. CSRF without tokens

Algorithm for unsafe methods (not GET/HEAD/OPTIONS), unless the route has `csrf=False`:

1. `Sec-Fetch-Site` present: `same-origin` or `none` → allow; otherwise continue to 3 (a trusted
   origin may still allow it).
2. No `Sec-Fetch-Site` and no `Origin` → allow (non-browser clients, old browsers' same-origin
   navigations).
3. `Origin` equals the request's own origin (`scheme://host` from `Host`; scheme `https` when
   `x-forwarded-proto: https` comes from a trusted proxy) → allow.
4. `Origin` in `trusted_origins` or allowed by CORS → allow.
5. Otherwise → 403 "Cross-origin request refused".

Rationale: same as Go 1.25 `CrossOriginProtection` and fetch-metadata guidance; works for JSON APIs
and forms with no template plumbing.

## R4. CORS

- Config object `ferro.CORS(allow_origins, allow_methods=("GET","POST","PUT","PATCH","DELETE"),
  allow_headers=(), expose_headers=(), allow_credentials=False, max_age=600)`; `"*"` allowed
  without credentials only.
- Preflight = `OPTIONS` with `Origin` and `Access-Control-Request-Method`: answered by the engine
  (204 with allow headers if origin allowed, else 403 plain envelope). Actual responses get
  `access-control-allow-origin` (echoed origin, or `*`), `vary: origin`, credentials/expose headers.

## R5. Rate limiting

- Spec `N/(second|minute|hour|day)`, N ≥ 1. Algorithm: token bucket (capacity N, refill N per
  period) per key `(scope, client ip)`; scope is the route id for per-route limits or `global`.
- Storage: `HashMap` behind a `Mutex` sharded by key hash (16 shards). Buckets idle for a full
  period are equivalent to a full bucket → evicted by a sweep every N insertions, keeping memory
  proportional to active clients (SC-004).
- 429 envelope with `retry-after` = seconds until one token is available (ceil, ≥ 1).

## R6. Client address and proxies

`trusted_proxies`: list of IPs/CIDRs. If the peer is trusted, walk `x-forwarded-for` from the
right, skipping trusted addresses; the first untrusted one is the client. Used by rate limiting,
access logs, CSRF scheme detection and `request.client`.

## R7. Headers and cookies

- Default headers: existing two + `x-frame-options: DENY`, `cross-origin-opener-policy:
  same-origin`. `hsts=True` → `strict-transport-security: max-age=31536000; includeSubDomains`
  (`hsts_max_age` setting); `content_security_policy="..."` → header as given.
- Cookies: request `cookie` header parsed in Rust (`request.cookies` dict, last value wins);
  `Response.set_cookie` builds RFC 6265 `set-cookie` in Python with validation of name (token) and
  value (cookie-octets, otherwise rejected — callers encode). `secure=None` → resolved at send time
  from the app's mode (the bridge knows `debug`).

## R8. Settings surface

`App(...)` / CLI / env: `rate_limit` (str), `trusted_proxies` (list; env comma-separated),
`trusted_origins` (list), `csrf` (bool, default true), `hsts` (bool), `hsts_max_age` (int),
`content_security_policy` (str), `cors` (App only: a `CORS` object). Route decorators gain
`rate_limit=` and `csrf=`.
