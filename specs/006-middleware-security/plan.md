# Implementation Plan: Middleware & Security Defaults

**Branch**: `006-middleware-security` | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

## Summary

Security policy (CSRF origin check, CORS, header set, client address, rate limiting) lives in a
new engine-independent `ferro_core::security` module and is applied by the HTTP service before
dispatch, so refusals never reach Python. Python gains `@app.middleware` (composed around route
endpoints at server start), `request.state`, `request.cookies` and `Response.set_cookie`
([research.md](research.md)).

## Technical Context

**Language/Version**: Rust 1.99; Python ≥ 3.12 · **Dependencies**: none new
**Testing**: `cargo test` (policy units: origin rules, CORS decisions, buckets, CIDR parsing,
cookie parsing), pytest with the in-process `serve` fixture (every scenario), benchmark rerun of
Feature 001 hello world (SC-002) and a 1,000-address limiter stress (SC-004)

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| I | PASS | Policies in Rust; Python only composes middleware and builds cookies. |
| II | PASS | Policies in `ferro-core`, not in the engine adapter. |
| III | PASS | No ORM changes. |
| IV | PASS | No SQL. |
| V | PASS | CSRF on by default, extra headers by default, secure cookies, bounded limiter memory. |
| VI | PASS | Refusals answered before dispatch; no middleware → no extra Python. |
| VII | PASS | Tests first for each story. |
| VIII | PASS | SC-002/SC-004 measured. |
| IX | PASS | Middleware signature like Starlette/FastAPI; `set_cookie` like Django. |
| X | PASS | Roadmap Feature 006; sessions/auth stay in 007. |

## Project Structure

```text
crates/core/src/security/{mod,origin,cors,ratelimit,proxy,cookies}.rs   policy logic (new)
crates/core/src/settings.rs          security settings
crates/core/src/headers.rs           extended default headers
crates/http/src/service.rs           apply policies, CORS headers, 403/429 envelopes
crates/router                        per-route options (rate limit, csrf)
crates/python-bridge/src/{server,request}.rs   settings/route options in, state/cookies/client out
python/ferro/{app,middleware,response,config,security}.py
tests/python/test_middleware.py, test_security.py, test_rate_limit.py
tests/benchmarks/run_006.py
```
