# Troubleshooting and FAQ

## Errors and what to do

### `403 Cross-origin request refused`

A browser sent a POST/PUT/PATCH/DELETE that does not look same-site.

- The page is on another origin (another port counts): add it to `trusted_origins` or to CORS
  `allow_origins`.
- Behind a proxy that terminates HTTPS: Ferro sees `http` and the browser's `Origin` says
  `https`. Set `FERRO_TRUSTED_PROXIES` to the proxy address so `X-Forwarded-Proto` is believed.
- A webhook from another service: `@app.post(..., csrf=False)` and verify its signature.

### Cookies or login do not work in production

Cookies are `Secure` outside `ferro dev`, so browsers only send them over HTTPS. Serve the site
over HTTPS (or, for a local test of `ferro run`, use `ferro dev`).

### Every client gets `429` together / all clients have the same IP

You are behind a proxy without `FERRO_TRUSTED_PROXIES`: every request seems to come from the
proxy and shares one budget. Set it to the proxy's addresses.

### `RelatedNotLoaded: Product.category is not loaded`

Ferro never runs a hidden query when you read a relation. Load it:
`Product.objects.select_related("category")`, `.prefetch_related("tags")` or
`await obj.fetch_related("category")`. `product.category_id` is always available.

### `TypeError: Note.owner must be a User instance, got User`

Two modules define a model with the same class name, and a relation names it as a string
(`ForeignKey("User", ...)`) before it was defined, so the name was resolved to the other
module's model. Use the full path (`ForeignKey("myapp.models.User", ...)`) or the class itself.

### `naive datetime; use a timezone-aware value such as datetime.now(UTC)`

`DateTime` fields store UTC and refuse ambiguous times. Use
`datetime.now(UTC)` / `datetime(..., tzinfo=UTC)`.

### `cannot add a non-null field to the existing table ... without a default`

Give the new field a constant `default=...`, or `null=True`, or add it nullable, fill it, and
make it required in a later migration. See [Migrations](05-migrations.md#rules-and-safety).

### `the database history does not match the migration files`

A migration file that was applied is missing or renamed, or files were reordered. Restore the
file (from version control) — never renumber applied migrations.

### `503` with `retry-after: 1` and `DatabaseUnavailableError` in the log

The database could not be reached, a connection was lost, a lock wait expired (`nowait`), or
too many queries were waiting (`queue_size`). Check the database, the URL, `pool_size` versus the
server's connection limit, and slow queries (`ferro_db_query_duration_seconds`). `/readyz`
reports `database` meanwhile.

### SQLite: writes wait, or fail with 503 under heavy writing

SQLite allows one writer at a time; a long `transaction()` blocks other writers (up to 5 s).
Keep transactions short; for several processes writing a lot, use PostgreSQL.

### `ORM queries must be awaited inside async code`

The ORM is async. Use it from `async def` handlers, `ferro shell`, or `asyncio.run(...)` in
scripts — not from plain `def` handlers.

### `TransactionError: select_for_update() must run inside a transaction`

Wrap the locking query and the writes in `async with transaction():`.

### `could not bind to 127.0.0.1:8000` (exit code 3)

Another program uses the port: `--port 8001`, or stop the other program.

### `invalid setting \`tls_key\`: the key in '...' does not match the certificate in '...'`

The key and certificate files are from different certificates; with Let's Encrypt use
`fullchain.pem` with `privkey.pem` of the same directory.

### `could not import module 'app'`

Run the command in the project directory, or pass the target (`ferro run mypkg.web:app`).
Errors inside your module are shown with their traceback.

### Handler timeouts (`503` after 30 s)

A handler exceeded `request_timeout`. Look for blocking calls in `async def` handlers (they
stall the loop: move them to a plain `def` handler or use an async library), slow queries, or
raise the timeout for genuinely long work (better: move it to a background job).

### An HTTP/2 client hangs uploading a body that is too large

Ferro answers `413` as soon as it knows the body is too large; clients that upload everything
before reading the answer can wait for the upload to finish. Browsers and curl read the early
answer. Raise `max_body_size` if large bodies are expected.

## FAQ

**Do I need Gunicorn, Uvicorn or Daphne?** No. `ferro run` is the production server
(`--processes N` for more CPUs). Put a reverse proxy in front only for what it adds (TLS
certificates for several sites, static files, port 80 redirects).

**Is Ferro WSGI or ASGI?** Neither. The Rust engine calls your Python handlers directly, so WSGI
and ASGI middleware or servers do not apply.

**Can I use Jinja2, httpx, Pydantic and other libraries?** Yes — any Python library. Use async
libraries inside `async def` handlers; blocking ones in plain `def` handlers.

**Can I use SQLAlchemy or the Django ORM?** Ferro's admin, auth, migrations and cache are built on
its own ORM. Other database libraries can be used in your own code, but they do not integrate
with those features.

**Static files?** Not served by Ferro; use a CDN, object storage, or your reverse proxy.

**WebSockets, server-sent events, file-upload parsing (`multipart/form-data`)?** Not yet.
Uploads can be sent as the raw request body.

**Which Python versions?** CPython 3.12 and newer.

**Windows?** Fully supported for development and production, including `--processes`.

**Where do I report a security problem?** Privately, as described in
[SECURITY.md](../../SECURITY.md).
