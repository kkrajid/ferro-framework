# Feature Specification: Runtime Core & Python Handlers

**Feature Branch**: `001-runtime-core`

**Created**: 2026-10-10

**Status**: Draft

**Input**: User description: "Feature 001 from docs/roadmap.md — the Ferro runtime core and Python
bridge (product vision Phases 1–2, §12–14, §25). A Python developer creates an `App`, registers
async route handlers with decorators, and runs them on Ferro's high-performance server through
`ferro dev` / `ferro run`. Covers routing, path and query parameters, JSON request and response
bodies, error handling, configuration, logging and graceful shutdown. First milestone:
`@app.get('/')` returning `{'message': 'Hello from Rust'}` works end to end."

## Clarifications

### Session 2026-10-10

- Q: What does `ferro run --workers N` mean in production? → A: Threads now, processes later. One
  OS process; N network I/O worker threads; Python handlers share one Python execution thread
  (plus a bounded thread pool for sync handlers). Multi-process serving is deferred to Feature 010.
- Q: When a request path differs from a registered route only by a trailing slash, what happens? →
  A: Respond 308 Permanent Redirect to the registered form (method and body preserved).

## User Scenarios & Testing *(mandatory)*

The actor in every story is an **application developer**: a Python developer building a web
application or API with Ferro. They know Python (and often Django or FastAPI) but are not expected
to know anything about the server engine underneath. A second actor, the **API client**, is any
HTTP client (browser, `curl`, another service) calling the developer's application.

### User Story 1 - Hello World in minutes (Priority: P1)

A developer installs Ferro, writes a single Python file that creates an application and one async
route returning a dictionary, starts the development server with one command, and receives that
dictionary as JSON from a browser or `curl`.

```python
from ferro import App

app = App()

@app.get("/")
async def home():
    return {"message": "Hello from Rust"}
```

**Why this priority**: This is the product's first milestone (vision §25). It proves the whole
chain — server, routing, Python handler execution and response conversion — and is the first
experience every new user has.

**Independent Test**: In a clean environment, install the package, save the file above as
`app.py`, run `ferro dev`, request `GET /` and verify status 200, a JSON content type and the body
`{"message": "Hello from Rust"}`.

**Acceptance Scenarios**:

1. **Given** the file above saved as `app.py` in the current directory, **When** the developer runs
   `ferro dev`, **Then** a server starts on `127.0.0.1:8000` and prints the address it is
   listening on.
2. **Given** the running server, **When** a client sends `GET /`, **Then** the response has status
   200, header `content-type: application/json` and body `{"message": "Hello from Rust"}`.
3. **Given** the running server, **When** a client sends `GET /missing`, **Then** the response has
   status 404 and a JSON error body.
4. **Given** the running server, **When** a client sends `POST /`, **Then** the response has status
   405 and an `allow` header listing `GET` (and `HEAD`).
5. **Given** the running server, **When** the developer presses Ctrl+C, **Then** the server stops
   cleanly within the shutdown timeout and the process exits with code 0.

---

### User Story 2 - Path and query parameters (Priority: P1)

A developer declares routes containing path parameters (`/products/{id}`) and reads path and query
values as handler arguments, with optional type conversion based on the handler's type hints.

```python
@app.get("/products/{id}")
async def product(request, id: int, currency: str = "INR"):
    return {"id": id, "currency": currency, "path": request.path}
```

**Why this priority**: Virtually every real API needs identifiers in the URL and query options;
without them Ferro cannot host any realistic endpoint.

**Independent Test**: Register the route above, request `/products/42?currency=USD` and
`/products/abc`, and verify the converted values and the validation error.

**Acceptance Scenarios**:

1. **Given** the route above, **When** a client requests `GET /products/42?currency=USD`, **Then**
   the handler receives `id == 42` (an `int`) and `currency == "USD"`, and the response is
   `{"id": 42, "currency": "USD", "path": "/products/42"}`.
2. **Given** the route above, **When** a client requests `GET /products/42`, **Then** `currency`
   takes its default `"INR"`.
3. **Given** the route above, **When** a client requests `GET /products/abc`, **Then** the response
   is status 422 with a JSON body naming the parameter `id` and the expected type.
4. **Given** a handler parameter without a default that is neither a path parameter nor `request`,
   **When** a client omits that query parameter, **Then** the response is status 422 naming the
   missing parameter.
5. **Given** a handler that accepts `request`, **When** any request arrives, **Then**
   `request.query` exposes all query parameters, including repeated keys as multiple values.
6. **Given** two routes `/users/me` and `/users/{id}`, **When** a client requests `/users/me`,
   **Then** the static route wins over the parameterized one regardless of registration order.

---

### User Story 3 - JSON APIs with every HTTP method (Priority: P2)

A developer builds a CRUD-style JSON API: they register handlers for GET, POST, PUT, PATCH and
DELETE, read JSON request bodies, and control status codes and headers of responses.

```python
from ferro import App, Response

@app.post("/products")
async def create_product(request):
    data = await request.json()
    return Response(data, status=201, headers={"location": "/products/1"})
```

**Why this priority**: Write operations and explicit status codes are required for any API beyond
read-only endpoints, but the read path (Stories 1–2) delivers value first.

**Independent Test**: Register the handler above, POST a JSON document, and verify status 201,
the `location` header and the echoed body.

**Acceptance Scenarios**:

1. **Given** decorators `get`, `post`, `put`, `patch`, `delete` and a generic
   `route(path, methods=[...])`, **When** the developer registers handlers, **Then** each responds
   only to its declared methods; `HEAD` is answered automatically for every `GET` route.
2. **Given** the handler above, **When** a client POSTs `{"name": "Laptop"}` with
   `content-type: application/json`, **Then** `await request.json()` returns
   `{"name": "Laptop"}` and the response has status 201 and the `location` header.
3. **Given** a POST with a malformed JSON body, **When** the handler calls `await request.json()`,
   **Then** the client receives status 400 with a JSON error body and the handler code after the
   call does not run.
4. **Given** handlers returning a `dict` or `list`, a `str`, `bytes`, `None`, or a `Response`,
   **Then** clients receive respectively JSON (`application/json`), text
   (`text/plain; charset=utf-8`), binary (`application/octet-stream`), an empty 204, or exactly the
   status/headers/body set on the `Response`.
5. **Given** a request whose body exceeds the configured maximum size (default 1 MiB), **When** it
   arrives, **Then** the client receives status 413 and the handler is not invoked.
6. **Given** a handler, **When** it reads `request.method`, `request.path`, `request.headers`
   (case-insensitive), `request.query`, `request.path_params`, `request.client` and
   `await request.body()`, **Then** each returns the corresponding request data.

---

### User Story 4 - Safe, helpful error handling (Priority: P2)

When a handler fails, API clients get a consistent JSON error and never see internals in
production, while the developer gets full detail during development.

```python
from ferro import HTTPException

@app.get("/orders/{id}")
async def order(id: int):
    raise HTTPException(404, "Order not found")
```

**Why this priority**: Secure-by-default error output is a constitutional requirement (Principle
V) and every later feature relies on a consistent error format.

**Independent Test**: Register one handler raising `HTTPException` and one raising
`ValueError`; call both in development and in production mode and compare bodies and logs.

**Acceptance Scenarios**:

1. **Given** the handler above, **When** a client requests `/orders/7`, **Then** the response is
   status 404 with body `{"error": {"status": 404, "message": "Order not found"}}`.
2. **Given** a handler raising an unexpected exception and the server in **production** mode,
   **When** a client calls it, **Then** the response is status 500 with body
   `{"error": {"status": 500, "message": "Internal Server Error", "request_id": "<id>"}}` and
   contains no traceback, file path, SQL or engine type names; the full traceback is written to
   the server log with the same request id.
3. **Given** the same handler and the server in **development** mode, **When** a client calls it,
   **Then** the 500 response additionally contains the exception type, message and traceback.
4. **Given** every error response produced by Ferro itself (404, 405, 400, 413, 422, 500, 503),
   **Then** all share the same JSON error envelope shape.
5. **Given** a handler that exceeds the configured request timeout (default 30 s), **When** the
   timeout elapses, **Then** the client receives status 503 and the server keeps serving other
   requests.

---

### User Story 5 - Development and production commands (Priority: P2)

A developer uses `ferro dev` while coding (debug detail, automatic reload on file change) and
`ferro run` in production (multiple workers, no debug detail), configures host, port and other
settings through command-line options, environment variables or `App(...)` arguments, and lists
registered routes with `ferro routes`.

**Why this priority**: The product promise includes a production server, not only a dev server,
and developers need to see and configure what is running.

**Independent Test**: Start the same application with `ferro dev` and `ferro run --workers 4
--port 9000`, edit the file while `ferro dev` runs, and run `ferro routes`; verify behavior in
each case.

**Acceptance Scenarios**:

1. **Given** an application module, **When** the developer runs `ferro dev`, **Then** the server
   runs in development mode, logs every request, and restarts automatically within 2 seconds
   after a `.py` file in the project changes; a syntax error after an edit is reported and the
   server resumes once the file is fixed.
2. **Given** an application module, **When** the developer runs `ferro run`, **Then** the server
   runs in production mode with debug detail disabled and auto-reload off.
3. **Given** the application target is not `app:app`, **When** the developer passes
   `ferro dev myproject.main:application`, **Then** that module and attribute are loaded; an
   invalid target produces a clear error naming the module or attribute and exits non-zero.
4. **Given** settings provided at several levels, **When** the server starts, **Then** precedence
   is: command-line option > environment variable (`FERRO_HOST`, `FERRO_PORT`, `FERRO_WORKERS`,
   `FERRO_DEBUG`, `FERRO_MAX_BODY_SIZE`, `FERRO_REQUEST_TIMEOUT`, `FERRO_LOG_LEVEL`) >
   `App(...)` argument > built-in default.
5. **Given** an application with routes, **When** the developer runs `ferro routes`, **Then** a
   table of method(s), path and handler name (module-qualified) is printed without starting a
   server.
6. **Given** a running server, **When** it receives SIGTERM (or Ctrl+C), **Then** it stops
   accepting new connections, lets in-flight requests finish for up to the shutdown timeout
   (default 30 s), and then exits.
7. **Given** a request is served, **Then** one access-log line is emitted containing method, path,
   status, duration and request id, and every response carries an `x-request-id` header.

---

### User Story 6 - Synchronous handlers without stalling the server (Priority: P3)

A developer who writes a plain `def` handler (for example, calling a blocking library) gets
correct results, and other requests keep being served while that handler runs.

**Why this priority**: Many existing Python libraries are synchronous; supporting them safely
widens adoption, but async handlers are the primary path.

**Independent Test**: Register a sync handler that sleeps for 1 second; fire 20 concurrent
requests to it plus requests to a fast async route; verify the fast route keeps responding
quickly.

**Acceptance Scenarios**:

1. **Given** a `def` (non-async) handler, **When** it is called, **Then** it returns the same
   kinds of responses as async handlers.
2. **Given** a sync handler blocking for 1 second under concurrent load, **When** clients call a
   fast async route at the same time, **Then** the fast route's responses are not delayed by the
   blocking handler beyond normal scheduling overhead.

---

### Edge Cases

- Duplicate registration of the same method and path: application startup fails with an error
  naming both handlers.
- Route path syntax errors (unbalanced braces, empty parameter name, duplicate parameter name):
  startup fails with an error naming the route.
- A handler declares a parameter that cannot be supplied (not a path parameter, not `request`, has
  an unsupported annotation): startup fails naming the handler and parameter.
- Trailing slash: if `/products/` is requested and only `/products` exists (or the reverse), the
  client receives 308 with `location` set to the registered form (query string preserved). If
  both forms are registered, each is served by its own handler and no redirect happens.
- Percent-encoded path segments (`/files/a%20b`) are decoded before being passed to handlers;
  encoded slashes do not split segments.
- Handler returns a value that cannot be serialized to JSON (e.g. a `set` or custom object): the
  client receives 500 and the log/dev response names the handler and the offending type.
- Non-UTF-8 or non-JSON content type sent to `request.json()`: status 400.
- Client disconnects mid-request: the handler's work is cancelled where possible and no error is
  logged as a server fault.
- Port already in use: the command fails immediately with a message naming the host and port.
- Unicode in path, query and JSON bodies is preserved exactly.
- Very many concurrent slow clients: the server keeps accepting up to its connection limit and
  rejects beyond it rather than exhausting memory.

## Requirements *(mandatory)*

### Functional Requirements

**Application & routing**

- **FR-001**: The framework MUST provide an `App` object that holds routes and configuration and
  is importable as `from ferro import App`.
- **FR-002**: Developers MUST be able to register handlers with `@app.get`, `@app.post`,
  `@app.put`, `@app.patch`, `@app.delete` and `@app.route(path, methods=[...])`.
- **FR-003**: Route paths MUST support named parameters `{name}`, each matching exactly one path
  segment; static segments MUST take precedence over parameter segments.
- **FR-004**: The system MUST answer `HEAD` for every `GET` route (same headers, empty body) and
  respond 405 with an `allow` header when the path matches but the method does not.
- **FR-004a**: When no route matches a path but the same path with a trailing slash added or
  removed does match, the system MUST respond 308 with `location` pointing to the matching form,
  preserving the query string.
- **FR-005**: Invalid route declarations (syntax errors, duplicates, unsatisfiable handler
  parameters) MUST be reported when the application starts, naming the route and handler.

**Handler invocation**

- **FR-006**: Handlers MAY be `async def` or plain `def`; plain handlers MUST run without blocking
  the server's request-processing threads.
- **FR-007**: Handler parameters MUST be filled by name: `request` receives the request object;
  names matching a path parameter receive the path value; any other parameter receives the query
  parameter of the same name, using its default when absent.
- **FR-008**: When a parameter is annotated `int`, `float`, `bool`, `str` (or `Optional` of
  those), the value MUST be converted; a failed conversion or missing required value MUST produce
  status 422 with an error naming the parameter and expected type.

**Request**

- **FR-009**: The request object MUST expose method, path, decoded path parameters, query
  parameters (multi-valued), case-insensitive headers, client address, raw body
  (`await request.body()`) and parsed JSON (`await request.json()`).
- **FR-010**: Malformed JSON or a non-JSON content type passed to `request.json()` MUST yield
  status 400.
- **FR-011**: Request bodies larger than the configured maximum (default 1 MiB) MUST be rejected
  with 413 before the handler runs.

**Response**

- **FR-012**: Handler return values MUST be converted as follows: `dict`/`list` → JSON with
  `application/json`; `str` → `text/plain; charset=utf-8`; `bytes` → `application/octet-stream`;
  `None` → 204 with empty body; `Response` → exactly the status, headers and body it carries.
- **FR-013**: The framework MUST provide `Response` (with `status`, `headers`, `content`,
  `media_type`) and `JSONResponse` objects importable from `ferro`.
- **FR-014**: JSON encoding MUST support `dict`, `list`, `tuple`, `str`, `int`, `float`, `bool`,
  `None`, `datetime`/`date`/`time` (ISO-8601), `Decimal` and `UUID` (as strings).

**Errors**

- **FR-015**: The framework MUST provide `HTTPException(status, message, headers=None)`; raising
  it MUST produce the matching status with the standard error envelope.
- **FR-016**: All framework-generated errors MUST use the envelope
  `{"error": {"status": <int>, "message": <str>, ...}}`; 422 errors MUST include a `details` list
  of `{param, location, problem}` entries.
- **FR-017**: In production mode, unexpected exceptions MUST produce a 500 envelope containing
  only status, generic message and request id; tracebacks MUST go to the log only.
- **FR-018**: In development mode, 500 responses MUST additionally include exception type,
  message and traceback.
- **FR-019**: Handlers exceeding the request timeout (default 30 s) MUST produce status 503
  without affecting other requests.

**Configuration & commands**

- **FR-020**: The command-line tool MUST provide `ferro dev [TARGET]`, `ferro run [TARGET]` and
  `ferro routes [TARGET]`, where `TARGET` is `module:attribute` and defaults to `app:app`.
- **FR-021**: `ferro dev` MUST run in development mode with request logging and automatic restart
  on changes to `.py` files under the project directory.
- **FR-022**: `ferro run` MUST run in production mode, in a single OS process, with a
  configurable number of network I/O worker threads (`--workers`, default: number of CPU cores)
  and no auto-reload. Async handlers execute on one shared Python execution thread; sync handlers
  execute on a bounded thread pool (default 40 threads). Multi-process serving is out of scope.
- **FR-023**: Configuration MUST be settable via command-line options, `FERRO_*` environment
  variables and `App(...)` arguments, with precedence CLI > environment > `App` > default, for at
  least: host (default `127.0.0.1`), port (default `8000`), workers, debug, max body size, request
  timeout, shutdown timeout, log level.
- **FR-024**: On SIGINT/SIGTERM the server MUST stop accepting connections, allow in-flight
  requests to complete up to the shutdown timeout, and exit with status 0.
- **FR-025**: Startup failures (port in use, import error, invalid target, invalid route) MUST
  print a single clear error message and exit non-zero.

**Observability & security defaults**

- **FR-026**: Every response MUST carry an `x-request-id` header (generated, or propagated from a
  valid incoming `x-request-id`), and every request MUST produce one access-log entry with method,
  path, status, duration and request id.
- **FR-027**: Every response MUST include the baseline security headers
  `x-content-type-options: nosniff` and `referrer-policy: strict-origin-when-cross-origin`;
  the `server` header MUST NOT reveal engine names or versions.
- **FR-028**: The server MUST enforce a maximum number of concurrent connections and a
  client-header read timeout with safe defaults.
- **FR-029**: No type, error message or log line visible to the developer's code or to API clients
  may name the underlying server engine, async runtime or bridge library.

**Developer experience**

- **FR-030**: The public Python API (`App`, `Request`, `Response`, `JSONResponse`,
  `HTTPException`) MUST ship with type hints usable by editors and type checkers.
- **FR-031**: The repository MUST include a runnable `examples/hello_world` application that
  exercises Stories 1–4.

### Key Entities

- **Application**: the developer's `App` instance; owns the route table and configuration.
- **Route**: method set + path pattern + handler; path pattern is a sequence of static and
  parameter segments.
- **Handler**: developer function (async or sync) with a parameter signature that determines what
  it receives.
- **Request**: one incoming HTTP request as seen by a handler: method, path, path params, query,
  headers, client, body.
- **Response**: status, headers and body produced from a handler's return value.
- **Configuration**: resolved settings (host, port, workers, debug, limits, timeouts, log level)
  plus the source each value came from.
- **Error envelope**: the standard JSON shape for every framework-generated error.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A Python developer new to Ferro gets the hello-world response from a running server
  within 5 minutes of starting installation, following only the README.
- **SC-002**: On a 4-core development machine, the hello-world route sustains at least 10,000
  requests per second with 100 concurrent clients and zero failed requests over a 30-second run
  (single process, per the clarified concurrency model).
- **SC-003**: At 1,000 concurrent clients for 60 seconds, the server completes all requests with
  zero errors and its memory use stays within 2× of its idle footprint after the run.
- **SC-004**: While one route is blocked in slow synchronous work under concurrent load, the 99th
  percentile latency of a fast route stays under 50 ms.
- **SC-005**: After a source edit, `ferro dev` serves the new code within 2 seconds in 95% of
  trials.
- **SC-006**: 100% of production-mode 500 responses in the test suite contain no traceback, file
  path or engine name.
- **SC-007**: Graceful shutdown completes all in-flight requests that finish within the shutdown
  timeout: 0 dropped requests in a test with 50 in-flight requests at SIGTERM.

## Assumptions

- Developers use CPython 3.12 or newer on Linux, macOS or Windows (constitution).
- HTTP/1.1 over plain TCP is in scope; TLS termination, HTTP/2, WebSockets, streaming responses,
  static file serving and file uploads (multipart) are deferred to later features.
- User-facing middleware, CORS, CSRF, rate limiting, sessions and cookies-based auth are delivered
  by Features 006–007; this feature only provides the baseline headers, limits and timeouts listed.
- Form-encoded bodies are not parsed in this feature; `request.body()` gives raw access.
- `ferro new` (project generator) is delivered by Feature 009; this feature uses a hand-written
  `app.py`.
- Performance figures in SC-002–SC-004 are measured locally with a standard HTTP load tool; the
  permanent benchmark suite against a raw-engine baseline is Feature 010.
- Multi-process serving (one Python interpreter per process, using all cores for Python code) is
  delivered in Feature 010; Feature 001 serves from a single process.
- Auto-reload restarts the application process; in-memory state is not preserved across reloads.
- Default bind address is loopback (`127.0.0.1`) so a dev server is never exposed by accident;
  production deployments set the host explicitly.
