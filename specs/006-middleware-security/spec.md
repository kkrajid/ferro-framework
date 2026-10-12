# Feature Specification: Middleware & Security Defaults

**Feature Branch**: `006-middleware-security`

**Created**: 2026-10-11

**Status**: Draft

**Input**: User description: "Feature 006 from docs/roadmap.md: middleware API, secure headers,
CORS, CSRF, limits, timeouts, rate limiting (vision §15: defaults are safe; developers explicitly
weaken security rather than enabling each protection)."

## Clarifications

### Session 2026-10-11

The user asked to continue without interruptions; recommended options were taken and recorded.

- Q: How is CSRF prevented? → A: By checking where browser requests come from, without tokens:
  for unsafe methods (POST, PUT, PATCH, DELETE) a request whose `Sec-Fetch-Site` is
  `cross-site`, or whose `Origin` does not match the request's host or a trusted origin, is
  refused with 403. Requests without these headers (non-browser clients, same-origin forms in
  old browsers) pass. On by default; trusted origins and per-route exemptions are configurable.
  This is the approach of modern browsers' fetch metadata and Go's cross-origin protection; a
  token scheme is not needed for cookie sessions (Feature 007).
- Q: Is rate limiting on by default? → A: No. A sensible universal default does not exist (one
  office behind a NAT, load tests); it is one setting away (`rate_limit="100/minute"` per client
  IP, globally or per route). Limits, timeouts and the connection cap from Feature 001 stay on.
- Q: Does Python middleware run for every request? → A: For requests that reach a route. Requests
  answered by the engine itself (404, 405, redirects, limits, CORS preflight, CSRF and rate-limit
  refusals) never run Python code, so they stay fast and safe under attack.
- Q: Are cookies `Secure` by default? → A: Yes in production; in development mode (`ferro dev`)
  they are not, so `http://127.0.0.1` works. `HttpOnly` and `SameSite=Lax` are always the
  defaults.
- Q: Which security headers are added? → A: The Feature 001 headers plus
  `x-frame-options: DENY` and `cross-origin-opener-policy: same-origin`. HSTS and a
  Content-Security-Policy are opt-in settings (they depend on TLS and the page's resources).
  Handlers can override any header.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Middleware (Priority: P1)

```python
@app.middleware
async def timing(request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    response.headers.append(("x-elapsed-ms", f"{(time.perf_counter() - started) * 1000:.1f}"))
    return response
```

**Why this priority**: Cross-cutting behaviour (auth checks, logging, headers, tenant selection)
needs one place; authentication (Feature 007) builds on it.

**Independent Test**: Register several middlewares and verify order, short-circuiting, header
changes, exception handling and that handler results of every kind arrive as `Response` objects.

**Acceptance Scenarios**:

1. **Given** middlewares registered in order A, B, **Then** a request passes A → B → handler and
   the response passes back B → A.
2. **Given** a middleware returning a response without calling `call_next`, **Then** the handler
   does not run and that response is sent.
3. **Given** a handler returning a dict, str, bytes, None or `Response`, **Then** `call_next`
   returns a `Response` with the same status, headers and body the client would have received.
4. **Given** a handler raising `HTTPException` or any exception, **Then** middleware sees the
   exception (and may handle it); unhandled ones produce the normal error envelope.
5. **Given** middleware storing values on `request.state`, **Then** the handler can read them.
6. **Given** no middleware, **Then** request handling has no extra cost (benchmark unchanged).

---

### User Story 2 - Browser security: CSRF, CORS, headers, cookies (Priority: P1)

**Why this priority**: Browser apps (admin, sessions) are unsafe without CSRF protection and
correct headers; APIs used from other sites need CORS.

**Independent Test**: Send same-site, cross-site, header-less and trusted-origin requests and CORS
preflights against a test app; inspect headers and cookies.

**Acceptance Scenarios**:

1. **Given** a POST with `Sec-Fetch-Site: cross-site` or an `Origin` of another site, **Then** 403
   with the error envelope; the handler does not run. Same-origin, same-site-trusted and header-less
   requests pass; GET/HEAD/OPTIONS are never blocked.
2. **Given** `trusted_origins=["https://app.example.com"]`, **Then** requests from that origin pass;
   **Given** a route registered with `csrf=False`, **Then** it is not checked.
3. **Given** `cors=CORS(allow_origins=["https://app.example.com"], allow_credentials=True)`,
   **Then** preflights from that origin get 204 with the allowed methods/headers/max-age and actual
   responses carry `access-control-allow-origin` (+ `vary: origin`); other origins get no CORS
   headers. CORS-allowed origins are also trusted for CSRF. Wildcard origins with credentials are
   refused at startup.
4. **Given** any response, **Then** it has `x-content-type-options`, `referrer-policy`,
   `x-frame-options: DENY` and `cross-origin-opener-policy: same-origin` unless the handler set
   them; with `hsts=True` / `content_security_policy=...` those headers are added too.
5. **Given** `request.cookies`, **Then** request cookies are parsed; `response.set_cookie(...)` and
   `delete_cookie(...)` produce valid `set-cookie` headers with `HttpOnly`, `SameSite=Lax`, `Path=/`
   and (outside development mode) `Secure` by default; invalid names/values are rejected.

---

### User Story 3 - Rate limiting (Priority: P2)

```python
app = App(rate_limit="100/minute")

@app.post("/login", rate_limit="5/minute")
async def login(...): ...
```

**Why this priority**: Protects login and expensive endpoints from abuse.

**Independent Test**: Exceed limits from one client and verify 429 responses, `retry-after`,
independence between clients and routes, and recovery after the window.

**Acceptance Scenarios**:

1. **Given** a limit of N per period, **When** a client sends N+1 requests within the period,
   **Then** the last gets 429 with `retry-after` and the error envelope; the handler does not run.
2. **Given** two clients, **Then** each has its own budget; **Given** a per-route limit, **Then** it
   applies to that route only and replaces the global one there.
3. **Given** `trusted_proxies=["10.0.0.0/8"]`, **Then** the client address comes from
   `x-forwarded-for` for requests from those proxies, and from the connection otherwise.
4. **Given** invalid limit strings (`"10/fortnight"`, `"0/minute"`), **Then** startup fails with a
   clear configuration error.

### Edge Cases

- `Origin: null` (sandboxed frames, file URLs) on an unsafe method is refused like any foreign
  origin unless explicitly trusted.
- A middleware that forgets to return a response: 500 envelope naming the middleware.
- A middleware calling `call_next` twice: the second call raises `RuntimeError`.
- Rate-limit state grows with distinct clients: idle entries are evicted so memory stays bounded.
- Behind a proxy without `trusted_proxies`, everyone shares the proxy's budget (documented).
- CORS preflight for a path that does not exist: answered like any other preflight (no leak of
  route existence); actual requests still get 404.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `@app.middleware` MUST register `async def (request, call_next) -> Response`
  functions applied in registration order (first registered is outermost) to every routed request.
- **FR-002**: `call_next` MUST return a `Response` for every handler result kind; responses MUST
  allow header changes; `request.state` MUST be a per-request namespace.
- **FR-003**: Unsafe-method requests from other origins MUST be refused (403) by the engine before
  dispatch, based on `Sec-Fetch-Site` and `Origin`, with `trusted_origins` and per-route `csrf=False`.
- **FR-004**: CORS MUST be configurable (`allow_origins`, `allow_methods`, `allow_headers`,
  `expose_headers`, `allow_credentials`, `max_age`), off by default, with preflights answered by the
  engine.
- **FR-005**: Security headers MUST include `x-frame-options: DENY` and
  `cross-origin-opener-policy: same-origin` by default; `hsts` and `content_security_policy` MUST
  be settings; handlers MAY override every header.
- **FR-006**: `request.cookies` and `Response.set_cookie` / `delete_cookie` MUST exist with secure
  defaults (HttpOnly, SameSite=Lax, Path=/, Secure outside development mode).
- **FR-007**: Rate limits (`N/second|minute|hour|day`) MUST be configurable globally and per route,
  per client address, answered with 429 + `retry-after` by the engine, with bounded memory.
- **FR-008**: The client address MUST honour `x-forwarded-for` only from `trusted_proxies`
  (addresses or CIDR ranges); `request.client` MUST report that address.
- **FR-009**: All new settings MUST follow the existing precedence (CLI > env > App > default) where
  they are scalar, and be validated at startup with clear errors.
- **FR-010**: Engine-side refusals MUST use the standard error envelope and request id and MUST be
  logged in the access log with their status.

### Key Entities

- **Middleware**: an async callable wrapping the handler chain.
- **Security policy**: CSRF rules, CORS rules, header set, cookie defaults.
- **Rate limit**: count and period; per-client token buckets keyed by route scope and address.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every acceptance scenario passes in automated tests.
- **SC-002**: Hello-world throughput without middleware stays within 5% of the Feature 001
  result; with one pass-through middleware the cost is reported.
- **SC-003**: Requests refused by CSRF, CORS preflight and rate limiting never reach Python
  (verified by a handler/middleware counter staying at zero).
- **SC-004**: Under 1,000 distinct client addresses hammering a rate-limited route, memory stays
  bounded and the limiter answers within the normal latency budget.

## Assumptions

- TLS termination and HTTP/2 remain out of scope (reverse proxy); HSTS is therefore opt-in.
- Token-based CSRF for HTML forms is not needed with the origin check; Feature 007 sessions rely
  on it plus `SameSite=Lax` cookies.
- Rate limits are per process (multi-process sharing is Feature 010's concern).
