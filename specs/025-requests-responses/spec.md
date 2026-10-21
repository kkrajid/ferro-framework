# Feature Specification: Requests and Responses at FastAPI/Flask Level

**Feature Branch**: `025-requests-responses`

**Created**: 2026-10-21

**Status**: Implemented

**Input**: "my ferro framework standard framework ayittulla fastapi, flask level performance,
request, response ellam cheyyumoo … please solve limitation and perform test". A comparison
found no file uploads, OpenAPI docs, static files, file responses, templates, dependency
injection, background tasks or reverse routing.

## Clarifications

### Session 2026-10-21

- Q: Uploads in memory or streamed to disk? → A: In memory, bounded by a per-route
  `max_body_size` (the engine already buffers bodies up to the limit). Multipart is parsed in
  Rust without copying (`ferro_core::multipart`), with limits on parts (1,000) and part headers
  (16 KiB). Streaming to disk is left for later.
- Q: Parameter API? → A: FastAPI-compatible spelling: `UploadFile` annotations, `Form()`,
  `Header()`, `Cookie()`, `Depends()`, `BackgroundTasks`. Problems from all sources in one 422.
- Q: Dependency cleanup timing? → A: Generator dependencies clean up when the handler returned
  (before the response is sent; FastAPI runs them after). Background tasks start after the
  handler returned and are awaited at shutdown.
- Q: Files? → A: `FileResponse` with media type, ETag, Last-Modified, 304, single-range 206/416,
  `Content-Disposition` with RFC 6266 filenames; bodies over 512 KiB streamed with
  `Content-Length` (Rust `SizedStream`). `app.static()` over a `{name:path}` route segment with
  path containment, hidden-file refusal and caching headers.
- Q: OpenAPI? → A: `app.add_openapi()` serves OpenAPI 3.1 and Swagger UI (jsDelivr, CSP);
  opt-in, so existing apps keep their routes and nothing is exposed by surprise.
- Q: Templates? → A: Jinja2 as an optional extra; autoescape on; `url_for` global.

## Requirements *(mandatory)*

- **FR-001**: Multipart uploads and per-route body limits.
- **FR-002**: Form, header and cookie parameters.
- **FR-003**: Dependencies and background tasks.
- **FR-004**: HTML, text, redirect and file responses; static files; `url_for`.
- **FR-005**: OpenAPI document and docs page.
- **FR-006**: Templates.
- **FR-007**: Documentation.

## Success Criteria *(mandatory)*

- **SC-001**: Every requirement tested over real HTTP; full suite green.
- **SC-002**: Existing handlers keep their fast paths (no new per-request work when the new
  parameter kinds are not used).
