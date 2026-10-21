# Forms, files, dependencies and API docs

## File uploads

```python
from pathlib import Path
from uuid import uuid4

from ferro import App, Form, HTTPException, UploadFile

app = App()
MEDIA = Path("media")


@app.post("/photos", max_body_size=10 * 1024 * 1024)        # this route accepts 10 MiB
async def upload(photo: UploadFile, caption: str = Form("")):
    if photo.content_type not in ("image/png", "image/jpeg"):
        raise HTTPException(415, "PNG or JPEG only")
    path = await photo.save(MEDIA / f"{uuid4().hex}.img")       # your name, never the client's
    return {"stored": path.name, "size": photo.size, "caption": caption}
```

| | |
|---|---|
| `photo: UploadFile` | one required file of field `photo` (`UploadFile \| None` optional, `list[UploadFile]` several) |
| `x: str = Form()` | a required form field (`Form("default")` optional), converted like query parameters |
| `await request.form()` | every field: `str` values and `UploadFile`s (`form.files`) |
| `upload.filename` / `raw_filename` | last path component with unsafe characters replaced / as sent |
| `upload.content_type`, `size`, `headers` | as sent by the client — check the content yourself |
| `await upload.read()` | the whole content, in memory |
| `async for chunk in upload.chunks()` | the content in 1 MiB pieces |
| `upload.file` | a seekable binary file object reading in pieces |
| `await upload.save(path, overwrite=False)` | writes in pieces in a worker thread; refuses to replace a file |

- The body is read completely, up to the route's `max_body_size` (default: the app-wide
  1 MiB), so the file is complete when the handler runs; larger bodies get `413`.
- A body over 1 MiB is written to an unnamed temporary file as it arrives (in `TMPDIR` /
  `TEMP`) and memory-mapped, not held in process memory; an `UploadFile` is a view of its part.
  Use `save()`, `chunks()` or `file` for large files — `read()` and `request.body()` copy
  everything into memory. The file disappears with the request. Give the temporary directory
  room for `max_body_size` times the concurrent uploads. For files of gigabytes, upload
  straight to object storage with a pre-signed URL.
- A Pydantic body parameter also accepts a multipart body (its text fields).
- Malformed multipart bodies get `400`; more than 1,000 parts or a part header over 16 KiB is
  refused.

## Headers, cookies and dependencies

```python
from ferro import Cookie, Depends, Header, HTTPException


async def current_user(token: str = Header(alias="x-api-token")):
    user = await User.objects.filter(api_token=token).first()
    if user is None:
        raise HTTPException(401, "Invalid token")
    return user


async def database():
    connection = await pool.acquire()
    try:
        yield connection             # code after yield runs when the handler returned
    finally:
        await pool.release(connection)


@app.get("/orders")
async def orders(
    user=Depends(current_user),
    conn=Depends(database),
    page: int = 1,
    user_agent: str = Header(""),    # header "user-agent"
    theme: str = Cookie("light"),
):
    ...
```

- A dependency's parameters are filled like a handler's (request, path, query, `Header`,
  `Cookie`, other `Depends`, `BackgroundTasks`); problems from all of them come back in one
  `422`.
- A dependency used several times in one request runs once (`Depends(f, use_cache=False)`
  runs it each time). Sync dependencies run in the worker threads.
- Dependencies cannot read the body (use a Pydantic, `Form` or `UploadFile` parameter on the
  handler), and WebSocket handlers do not take `Depends`.
- Ruff/flake8-bugbear: add `extend-immutable-calls = ["ferro.Depends", "ferro.Form",
  "ferro.Header", "ferro.Cookie"]`, as for FastAPI.

### Overriding dependencies in tests

```python
app.dependency_overrides[current_user] = lambda: fake_user     # until removed

with app.override_dependency(database, fake_database):          # inside the block only
    response = client.get("/notes")
```

Overrides apply at request time to every route, including dependencies used by other
dependencies; the replacement's own parameters are read from the request like any dependency.

### One transaction per request

```python
from ferro.db import request_transaction


@app.post("/orders")
async def place(data: OrderIn, _=Depends(request_transaction)):
    order = await Order.objects.create(...)
    await OrderLine.objects.bulk_create(...)
    return order
```

The handler's queries (and those of dependencies listed after it) run in one transaction: it
commits when the handler returned and rolls back when it raised, `HTTPException` included.
Background tasks start after the commit. A request-scoped service is a generator dependency:
it is created once per request (cached), shared by every dependency that asks for it, and
cleaned up when the handler returned.

### Security schemes

```python
from ferro.security import APIKeyHeader, HTTPBearer

bearer = HTTPBearer(bearer_format="JWT")


async def current_user(token: str = Depends(bearer)) -> User:
    user = await User.objects.filter(api_token=token).first()
    if user is None:
        raise HTTPException(401, "Invalid token", headers={"WWW-Authenticate": "Bearer"})
    return user
```

| Scheme | Gives | Reads |
|---|---|---|
| `HTTPBearer(bearer_format=None)` | the token | `Authorization: Bearer <token>` |
| `HTTPBasic(realm=None)` | `(username, password)` | `Authorization: Basic ...` (HTTPS only) |
| `APIKeyHeader(name=...)` | the key | a header |
| `APIKeyQuery(name=...)` | the key | a query parameter (logged by proxies: prefer a header) |
| `APIKeyCookie(name=...)` | the key | a cookie |

A missing credential is `401` (with `WWW-Authenticate` for the HTTP schemes) unless
`auto_error=False`, which gives `None`. The schemes only read credentials; checking them is
your dependency's job. Every scheme a route uses (directly or through dependencies) appears in
its OpenAPI `security`, and routes guarded by `login_required`, `staff_required` or
`permission_required` list the `sessionid` cookie.

## Background tasks

```python
from ferro import BackgroundTasks


@app.post("/signup")
async def signup(data: SignupIn, tasks: BackgroundTasks):
    user = await User.objects.create_user(data.username, password=data.password)
    tasks.add_task(send_welcome_email, user.email)       # async or plain function
    return {"id": user.pk}
```

Tasks start after the handler returned and run in order; an exception is logged and the next
task runs. A stopping server waits for running tasks up to `shutdown_timeout`. They live in the
server process: a crash loses them, so use a job queue for work that must happen. To run work
only after a transaction commits, use [`on_commit`](18-signals-and-hooks.md).

## Responses

| Class | For |
|---|---|
| `HTMLResponse(html)`, `PlainTextResponse(text)` | HTML pages, text |
| `RedirectResponse(url, status=307)` | redirects (`303` after a form POST); never to an unchecked address from the request |
| `FileResponse(path, filename=None, download=False, request=request)` | a file from disk |
| `JSONResponse`, `StreamingResponse`, `EventStream` | as before |

`FileResponse` sets the media type, size, `ETag` and `Last-Modified`; with `request=` it answers
`304` to conditional requests and `206` to a `Range` (resuming downloads, video seeking). Files
over 512 KiB are streamed from disk with their `Content-Length`.

## Static files

```python
app.static("/static", "static")                       # ./static/css/site.css → /static/css/site.css
app.url_for("static", path="css/site.css")            # "/static/css/site.css"
```

Paths stay inside the directory (`..`, absolute paths, drive letters and hidden files are
refused with `404`); responses carry `Cache-Control: public, max-age=3600` (`cache_max_age=`),
`ETag` and range support. `html=True` serves `index.html` for a directory. In production a
proxy or CDN in front of Ferro serves them faster still. A route's last path segment can also be
`{name:path}` (the rest of the path, slashes included) in your own routes.

## url_for

```python
@app.get("/products/{id}", name="product")
async def product(id: int): ...

app.url_for("product", id=7, ref="mail")              # "/products/7?ref=mail"
```

Path parameters are percent-encoded; other arguments become the query string. Routes without
`name=` are named after their handler (`module.function`).

## OpenAPI and Swagger UI

```python
app = App()
app.add_openapi(title="Shop API", version="1.0.0")    # /openapi.json and /docs


@app.post("/items", tags=["items"])
async def create_item(data: ItemIn, dry_run: bool = False) -> ItemOut:
    """Creates an item.

    The rest of the docstring is the description."""
```

The document (OpenAPI 3.1) lists every HTTP route except WebSockets, the admin and routes with
`include_in_schema=False`: path, query, header and cookie parameters with their types and
defaults, Pydantic bodies (JSON or form), form fields and uploads (`multipart/form-data`),
Pydantic return annotations as response schemas, security schemes, and the `422` error
format. `/docs` loads
Swagger UI from jsDelivr under a restrictive Content-Security-Policy; pass `docs_path=None` to
serve only the JSON, and put the docs behind authentication or leave them out in production if
your API is private.

Route options describe an operation further (and `status_code` also sets the response):

```python
@app.post(
    "/items",
    status_code=201,                         # for results that are not Response objects
    summary="Create an item",                # else the docstring's first line
    description="Stores a new item.",        # else the rest of the docstring
    responses={409: "Already exists", 404: {"description": "No shop", "model": Problem}},
    deprecated=True,
    operation_id="createItem",
    tags=["items"],
)
async def create_item(data: ItemIn) -> ItemOut: ...
```

A handler that returns a `Response` keeps its own status. `status_code=204` sends no body.
`app.add_openapi(servers=[{"url": "https://api.example.com"}])` lists servers.

## Templates

```bash
pip install "ferro-framework[templates]"
```

```python
from ferro.templating import Templates

templates = Templates("templates", app=app)


@app.get("/products/{id}")
async def product_page(id: int):
    item = await Product.objects.get_or_404(pk=id)
    return templates.render("product.html", {"product": item})
```

Jinja2 with autoescaping on (values are HTML-escaped unless marked `|safe`); `url_for(...)` is
available in templates. `templates.render_string(name, context)` returns the text.
