---
description: "Task list for Feature 025 — Requests and Responses"
---

# Tasks: Requests and Responses at FastAPI/Flask Level

**Input**: [spec.md](spec.md).

- [X] T001 Router `{name:path}` tail segments (last segment, lowest precedence) with tests
- [X] T002 `RouteOptions.max_body_size`, route tuple, per-route body limit in the HTTP service
- [X] T003 Streams with a known length use `SizedStream` (`Content-Length`)
- [X] T004 `ferro_core::multipart` parser (boundary, quoted/extended filenames, limits) with tests
- [X] T005 `Request.form()` returns `FormData` with `UploadFile`s for multipart bodies
- [X] T006 `ferro.uploads` (`UploadFile`, `FormData`), `ferro.params` (`Form`, `Header`, `Cookie`, `Depends`), `ferro.background`
- [X] T007 Routing: special parameter specs, dependency resolution with exit stacks and caching, rich endpoint, multipart Pydantic bodies
- [X] T008 Responses: `HTMLResponse`, `PlainTextResponse`, `RedirectResponse`; `ferro.files` (`FileResponse`, `StaticFiles`); bridge detects body iterators on any response
- [X] T009 `App.static`, `App.url_for`, `App.add_openapi`, route `max_body_size`/`include_in_schema`/`tags`; background tasks drained at shutdown
- [X] T010 `ferro.openapi`, `ferro.templating`; exports, public API list, stability policy, `templates` extra
- [X] T011 `TestClient(files=...)`; tests in `tests/python/test_requests_responses.py`
- [X] T012 Guide page 19, docs index, CHANGELOG

## Implementation Notes

- **Found while testing**: a range starting beyond the file computed a negative length (500);
  now 416, and malformed `Range` headers are ignored as RFC 9110 requires.
- **Limits**: uploads are held in memory (bounded per route); one byte range per request;
  generator dependencies clean up before the response is sent; background tasks are not
  persisted; Swagger UI assets come from a CDN.
