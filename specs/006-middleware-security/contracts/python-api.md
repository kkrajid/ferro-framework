# Contract: Python API (Feature 006)

```python
from ferro import App, CORS, Response

app = App(
    csrf=True,                               # default
    trusted_origins=["https://admin.example.com"],
    cors=CORS(allow_origins=["https://app.example.com"], allow_credentials=True),
    rate_limit="100/minute",                 # default None
    trusted_proxies=["10.0.0.0/8"],
    hsts=False, hsts_max_age=31536000,
    content_security_policy=None,
)

@app.middleware
async def mw(request, call_next):
    request.state.user = None
    response = await call_next(request)      # always a Response
    response.headers.append(("x-a", "1"))
    return response

@app.post("/login", rate_limit="5/minute", csrf=True)
async def login(request):
    request.cookies                          # dict[str, str]
    response = Response({"ok": True})
    response.set_cookie("sid", "abc", max_age=3600)   # HttpOnly; SameSite=Lax; Path=/; Secure*
    response.delete_cookie("old")
    return response
```

Settings precedence for scalar settings: CLI option > `FERRO_*` env > `App(...)` > default.
Env names: `FERRO_RATE_LIMIT`, `FERRO_TRUSTED_PROXIES` / `FERRO_TRUSTED_ORIGINS` (comma
separated), `FERRO_CSRF` (`true`/`false`), `FERRO_HSTS`, `FERRO_HSTS_MAX_AGE`,
`FERRO_CONTENT_SECURITY_POLICY`.

Engine refusals (error envelope, request id): 403 `Cross-origin request refused`, 403 for a
disallowed CORS preflight, 429 `Too Many Requests` + `retry-after`.

`request.client` → `(host, port)` of the client (after trusted proxies; port 0 when taken from
`x-forwarded-for`). `request.state` → per-request `types.SimpleNamespace`.
