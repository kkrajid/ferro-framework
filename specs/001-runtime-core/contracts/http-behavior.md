# Contract: HTTP Behavior (Feature 001)

## Status codes produced by Ferro

| Situation | Status | Extra headers | Body |
|---|---|---|---|
| Handler returned dict/list/tuple | 200 | `content-type: application/json` | JSON |
| Handler returned str | 200 | `content-type: text/plain; charset=utf-8` | text |
| Handler returned bytes | 200 | `content-type: application/octet-stream` | bytes |
| Handler returned None | 204 | — | empty |
| Handler returned `Response` | as set | as set | as set |
| No route for path | 404 | — | envelope |
| Path matches, method not allowed | 405 | `allow: <methods>` | envelope |
| Path matches only with trailing slash toggled | 308 | `location: <path>[?query]` | envelope |
| Invalid JSON / non-JSON content type in `request.json()` | 400 | — | envelope |
| Body larger than `max_body_size` | 413 | `connection: close` | envelope |
| Parameter conversion failure / missing query param | 422 | — | envelope with `details` |
| `HTTPException(status, message)` | status | exception headers | envelope |
| Unhandled exception / unserializable return value | 500 | — | envelope (debug adds detail) |
| Handler exceeded `request_timeout` | 503 | `retry-after: 1` | envelope |
| Server shutting down, new request on kept-alive conn | connection closed | — | — |

`HEAD` on a GET route: same status and headers as GET (including `content-length`), empty body.

## Error envelope

```json
{"error": {"status": 404, "message": "Not Found", "request_id": "5f0c…"}}
```

- 422 adds `"details": [{"param": "id", "location": "path|query", "problem": "…"}]`.
- Debug-mode 500 adds `"type"`, `"exception"`, `"traceback": ["…", …]`.
- Production 500: message is exactly `Internal Server Error`; no other fields besides
  `status`, `message`, `request_id`.

## Headers on every response

| Header | Value |
|---|---|
| `x-request-id` | propagated (if `^[A-Za-z0-9._-]{1,128}$`) or generated (32 hex chars) |
| `x-content-type-options` | `nosniff` |
| `referrer-policy` | `strict-origin-when-cross-origin` |
| `server` | absent |

Handler-supplied headers override the defaults above except `x-request-id`.

## Limits

| Limit | Default | On violation |
|---|---|---|
| Request body | 1 MiB | 413 before handler runs |
| Header read | 5 s | connection closed (408 if possible) |
| Request handling | 30 s | 503, handler task cancelled |
| Concurrent connections | 10,000 total | new connections wait in the accept backlog |

## Access log line (stderr, target `ferro.access`)

```text
<timestamp> INFO ferro.access: method=GET path=/products/42 status=200 duration_ms=0.41 request_id=5f0c… client=127.0.0.1
```
