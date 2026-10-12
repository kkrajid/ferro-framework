# Security

Ferro is secure by default: the protections below are on without configuration and run in the
Rust engine **before** any Python code. This page explains each one and how to adjust it. A
pre-launch checklist is at the end.

## What is on by default

| Protection | Default |
|---|---|
| Bind address | `127.0.0.1` (not reachable from other machines until you choose `--host 0.0.0.0`) |
| Request body limit | 1 MiB (`max_body_size`) → 413 |
| Header timeout / request timeout | 5 s / 30 s (slow clients get 408, slow handlers 503) |
| Connection cap | 10,000 (`max_connections`) |
| Cross-origin protection (CSRF) | on for unsafe methods |
| Security headers | on every response |
| Cookies | `HttpOnly`, `SameSite=Lax`, `Secure` outside `ferro dev` |
| Error responses | no tracebacks or internal details outside `ferro dev` |
| SQL | always bound parameters |
| Passwords | Argon2id; constant-time checks; no account enumeration by timing |
| Admin | escaped output, strict Content-Security-Policy, staff and permission checks |
| Metrics | off; when on, only for `metrics_allow` addresses |

## Cross-origin request protection (CSRF)

Browsers attach cookies to requests that other sites trigger (a hidden form posting to your
`/transfer`). Ferro refuses such requests with `403 Cross-origin request refused`:

- `GET`, `HEAD`, `OPTIONS` are never blocked — keep them free of side effects.
- `POST`, `PUT`, `PATCH`, `DELETE` from a browser are allowed when the browser says they come
  from this site (`Sec-Fetch-Site: same-origin`/`none`, or an `Origin` equal to this site's
  scheme, host and port).
- Requests without browser metadata (curl, mobile apps, server-to-server) are not affected.
- Other sites can be allowed with `trusted_origins=["https://app.example.com"]` or by listing
  them in CORS.

Exempt one route (webhooks signed by the sender) or turn the check off:

```python
@app.post("/webhooks/payments", csrf=False)
async def payment_webhook(request): ...      # verify the sender's signature yourself

app = App(csrf=False)                          # only for APIs that never use cookies
```

No tokens, hidden fields or JavaScript are needed: the check relies on headers every current
browser sends.

## CORS

Let browser code on other sites call your API:

```python
from ferro import App, CORS

app = App(cors=CORS(
    allow_origins=["https://app.example.com"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],   # default
    allow_headers=["authorization", "content-type"],
    expose_headers=["x-total-count"],
    allow_credentials=True,                                    # send cookies
    max_age=600,                                               # preflight cache, seconds
))
```

- Preflight `OPTIONS` requests are answered by the engine.
- Listed origins also pass the cross-origin check (their form posts are trusted).
- `allow_origins=["*"]` allows every site **without credentials** (combining it with
  `allow_credentials=True` is refused at startup); requests that carry cookies are still subject
  to the cross-origin check.

## Rate limits

```python
app = App(rate_limit="100/minute")             # every route, per client address

@app.post("/login", rate_limit="5/minute")     # this route's own budget
```

Format `N/second`, `N/minute`, `N/hour`, `N/day`. A client over its budget gets `429` with
`retry-after` (seconds). The budget is a token bucket: `N` requests at once, refilled
continuously. IPv6 clients are counted per /64 network (one household or server). With
`ferro run --processes N`, all processes of the machine share the budgets; separate machines
count separately (use your load balancer or gateway for global limits).

## Behind a proxy

When Ferro runs behind a reverse proxy or load balancer, every connection comes from the proxy.
Tell Ferro which addresses are proxies:

```bash
FERRO_TRUSTED_PROXIES=127.0.0.1,10.0.0.0/8
```

Then the client address (`request.client`, rate limits, logs, `metrics_allow`) comes from
`X-Forwarded-For` (the right-most address that is not a trusted proxy — clients cannot forge
it), and `X-Forwarded-Proto: https` marks requests as HTTPS for the cross-origin check. Without
this setting, `X-Forwarded-*` headers are ignored — and every client appears as the proxy, which
means one shared rate-limit budget. **Always set it behind a proxy.**

## HTTPS

Serve HTTPS either from a proxy/load balancer (most deployments) or from Ferro itself:

```bash
ferro run --host 0.0.0.0 --port 443 --tls-cert fullchain.pem --tls-key privkey.pem
```

TLS 1.2 and 1.3 only, HTTP/2 offered to clients that support it. Then turn on HSTS so browsers
never use plain HTTP again:

```python
app = App(hsts=True, hsts_max_age=31_536_000)   # FERRO_HSTS=1
```

## Response headers

Added to every response unless the handler set the header itself:

| Header | Value |
|---|---|
| `x-content-type-options` | `nosniff` |
| `referrer-policy` | `strict-origin-when-cross-origin` |
| `x-frame-options` | `DENY` |
| `cross-origin-opener-policy` | `same-origin` |
| `strict-transport-security` | with `hsts=True` |
| `content-security-policy` | with `content_security_policy="default-src 'self'"` |
| `x-request-id` | always |

## Cookies and sessions

`Response.set_cookie` defaults to `HttpOnly`, `SameSite=Lax`, `Path=/` and `Secure` (except in
`ferro dev`). Sessions ([Authentication](06-authentication.md)) store only a digest of the
cookie key, rotate the key at login, end after 30 days or on a password change.

## Data and queries

- Never build SQL strings: there is no raw-SQL API, and every ORM value is a bound parameter.
- Filter by owner in every query that serves user data
  (`Note.objects.get_or_404(pk=id, owner=request.user)`): answering 404 for other people's
  objects also hides that they exist.
- Return explicit dicts (`obj.to_dict()` or chosen fields); `User.to_dict()` leaves out the
  password hash.
- Validate input: type hints for parameters; check JSON bodies before using them, and raise
  `ValidationError` with every problem.

## Secrets

- Settings come from the environment or `.env`; `ferro new` writes `.env` readable only by its
  owner and lists it in `.gitignore`. Commit `.env.example`, never `.env`.
- Ferro needs no `SECRET_KEY`: sessions are random keys stored in the database.
- Database passwords never appear in error messages or logs.

## Pre-launch checklist

- [ ] `ferro run` (never `ferro dev` or `--debug`) in production
- [ ] HTTPS everywhere; `FERRO_HSTS=1` once it works
- [ ] `FERRO_TRUSTED_PROXIES` set to your proxy/load balancer addresses
- [ ] PostgreSQL URL with `sslmode=require` (or `verify-full`) for a remote database
- [ ] rate limits on login, registration and other expensive routes
- [ ] every query on user data filtered by owner or permission
- [ ] `/metrics` restricted (`metrics_allow`) or blocked at the proxy
- [ ] `.env` not committed; secrets in the platform's secret store
- [ ] `ferro makemigrations --check` in CI
- [ ] dependencies updated (`pip install --upgrade ferro`) when a security release appears —
      see [SECURITY.md](../../SECURITY.md)

The last full review is [docs/security-audit.md](../security-audit.md).
