# Routing, requests and responses

## The application

```python
from ferro import App

app = App()                    # settings: see the settings reference
```

`App(...)` takes every [setting](14-settings-reference.md) as a keyword argument
(`App(database_url=..., rate_limit="100/minute", cors=CORS(...))`). Command-line options and
`FERRO_*` environment variables override them. Run it with `ferro dev` / `ferro run` (they look
for `app` in `app.py`; another target: `ferro run mypackage.web:application`), or
`app.run(host=..., port=...)` from a script (one process).

`app.add_auth()` turns on users and sessions ([Authentication](06-authentication.md));
`app.add_admin()` mounts the admin and implies `add_auth()` ([Admin](07-admin.md)).

## Routes

```python
@app.get("/products")
async def list_products(): ...

@app.post("/products")
@app.put("/products/{id}")
@app.patch("/products/{id}")
@app.delete("/products/{id}")
@app.route("/search", methods=["GET", "POST"])
```

Options on every route decorator:

| Option | Effect |
|---|---|
| `rate_limit="5/minute"` | replaces the app-wide limit for this route (`N/second`, `/minute`, `/hour`, `/day`) |
| `csrf=False` | exempts the route from the cross-origin check (webhooks called by other services) |
| `name="..."` | the name shown by `ferro routes` and in logs (default: module and function) |

Matching rules:

- Path parameters are written `{name}` and match one path segment. `%2F` inside a segment is
  decoded after splitting, so it never creates a new segment.
- Static segments win over parameters regardless of order: `/users/me` beats `/users/{id}`.
- `HEAD` is answered for every `GET` route (headers only).
- A known path with the wrong method → `405` with an `allow` header; an unknown path → `404`.
- `/items/` → `308` redirect to `/items` when only `/items` exists, and the other way round
  (the query string is kept).
- Two routes with the same method and pattern are a startup error naming both handlers.

List them:

```bash
ferro routes
```

## Handlers

Handlers can be `async def` (preferred: thousands can wait on the database at once) or plain
`def`. Plain functions run on a thread pool (`sync_threads`, default 40), so a blocking call
(a slow library, `time.sleep`) never stalls other requests — but each one holds a thread, and
the ORM is async-only, so database work belongs in `async def` handlers.

A handler that runs longer than `request_timeout` (default 30 s) is cancelled (its transaction
rolls back) and the client gets `503`. A client that disconnects also cancels its handler.

## Parameters

Handler parameters are filled **by name**:

```python
@app.get("/products/{id}")
async def product(request, id: int, currency: str = "INR", in_stock: bool | None = None):
    ...
```

| Parameter | Value |
|---|---|
| `request` (or any name annotated `Request`) | the request object |
| a name that appears in the path | the path segment |
| any other name | the query parameter of that name — required unless it has a default |

Supported annotations: `str`, `int`, `float`, `bool` (`true/false`, `1/0`, `yes/no`,
`on/off`), each optionally `| None`; no annotation means `str`. Anything else is a startup
error. All problems of a request are reported together:

```json
{"error": {"status": 422, "message": "Validation failed", "request_id": "4f…",
  "details": [{"param": "id", "location": "path", "problem": "expected int, got 'abc'"},
              {"param": "page", "location": "query", "problem": "missing required query parameter"}]}}
```

Repeated query parameters: `request.query.getlist("tag")`.

## The request

| Attribute | |
|---|---|
| `request.method`, `request.path`, `request.raw_path`, `request.query_string` | strings |
| `request.query` | `get(name, default)`, `getlist(name)`, `items()`, `in`, `[name]` |
| `request.headers` | case-insensitive: `get`, `getlist`, `[name]`, `in` |
| `request.path_params` | `dict` of raw path values |
| `request.cookies` | `dict` |
| `request.client` | `(ip, port)` — the real client behind [trusted proxies](09-security.md#behind-a-proxy) |
| `request.request_id` | the id also sent as `x-request-id` and written in logs |
| `request.state` | a namespace for your own values (middleware → handler) |
| `request.user`, `request.session` | with `add_auth()` |
| `await request.body()` | `bytes` |
| `await request.json()` | parsed JSON (`400` when the body is not JSON) |
| `await request.form()` | fields of an `application/x-www-form-urlencoded` body (`get`, `getlist`) |

Bodies larger than `max_body_size` (default 1 MiB) are refused with `413` before your code runs;
a client still uploading when `request_timeout` expires gets `408`.

**File uploads**: `multipart/form-data` is not parsed. Send files as the raw body
(`content-type: application/octet-stream` or the file's type) and read `await request.body()`,
raising `max_body_size` for that need, or upload directly from the browser to object storage
with a pre-signed URL (the usual choice for large files).

## Responses

| Handler returns | Response |
|---|---|
| `dict`, `list`, `tuple` | JSON; `datetime`, `date`, `time`, `Decimal`, `UUID` become strings |
| a model instance | not directly: return `obj.to_dict()` (relations as `<name>_id`) |
| `str` | `text/plain; charset=utf-8` |
| `bytes` | `application/octet-stream` |
| `None` | `204 No Content` |
| `Response(content, status=200, headers=None, media_type=None)` | exactly that |
| `JSONResponse(content, status=200, headers=None)` | JSON even for a `str` |

```python
from ferro import JSONResponse, Response

return Response({"id": 7}, status=201, headers={"location": "/items/7"})
return Response("<h1>Hello</h1>", media_type="text/html; charset=utf-8")
return Response(csv_bytes, media_type="text/csv", headers={"content-disposition": "attachment; filename=report.csv"})
```

HTML: Ferro has no template engine; render with any library (for example Jinja2) and return the
string with `media_type="text/html; charset=utf-8"`. Escape user data — the template library's
autoescape does that.

### Cookies

```python
response = Response({"ok": True})
response.set_cookie("theme", "dark", max_age=30 * 24 * 3600)
response.delete_cookie("old")
```

Defaults: `HttpOnly`, `SameSite=Lax`, `Path=/`, and `Secure` everywhere except `ferro dev`.
Options: `max_age`, `expires` (aware `datetime`), `path`, `domain`, `secure`, `httponly`,
`samesite` (`"lax"`, `"strict"`, `"none"` — `none` requires `secure`). Values must be
cookie-safe (letters, digits, URL-safe symbols); encode other data first.

### Headers set for you

Every response gets `x-request-id` and the [security headers](09-security.md#response-headers)
unless the handler set them itself.

## Errors

```python
from ferro import HTTPException, ValidationError

raise HTTPException(404, "Order not found")
raise HTTPException(429, "Slow down", headers={"retry-after": "30"})
raise ValidationError([{"param": "email", "location": "body", "problem": "not an email"}])
```

All errors share one envelope:

```json
{"error": {"status": 404, "message": "Order not found", "request_id": "9b2…"}}
```

| Situation | Status |
|---|---|
| `HTTPException(status)` | that status (400–599) |
| bad parameters, `ValidationError` | 422 with `details` |
| body not JSON for `request.json()` | 400 |
| body too large | 413 |
| `Model.DoesNotExist` from `get_or_404` | 404 |
| database unreachable (`DatabaseUnavailableError`) | 503 with `retry-after: 1` |
| handler timeout | 503 |
| any other exception | 500 |

A `500` in production contains only a generic message and the request id; the traceback is in
the server log under the same id. In `ferro dev` the response includes the exception and
traceback.

## Middleware

```python
import time


@app.middleware
async def timing(request, call_next):
    started = time.perf_counter()
    response = await call_next(request)          # always a Response object
    response.headers.append(("x-elapsed-ms", f"{(time.perf_counter() - started) * 1000:.1f}"))
    return response


@app.middleware
async def require_api_key(request, call_next):
    if request.path.startswith("/api/") and request.headers.get("x-api-key") != API_KEY:
        return Response({"error": "forbidden"}, status=403)   # short-circuit
    request.state.client_name = "partner"                     # read in handlers
    return await call_next(request)
```

- Middleware runs for requests that match a route, in registration order (first = outermost).
- `call_next(request)` may be called once and returns a `Response` (handler results are
  converted). Exceptions raised by the handler (`HTTPException`, errors) pass through
  `call_next`; wrap it in `try`/`except` (or `try`/`finally`) when the middleware must act on
  failures too. A middleware must return a `Response`.
- An app without middleware pays nothing for it.
- Limits, CORS, cross-origin checks, rate limits, probes and metrics run in the engine before
  any middleware.

## Background work

There is no built-in task queue. Work that must survive the request (emails, reports) belongs in
a job queue or a separate worker process; short fire-and-forget work can use a database table
polled by a worker using `select_for_update(skip_locked=True)` (see
[Models](04-models-and-queries.md#locking-rows)). WebSockets and server-sent events are not
supported.
