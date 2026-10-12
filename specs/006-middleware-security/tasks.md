---
description: "Task list for Feature 006 — Middleware & Security Defaults"
---

# Tasks: Middleware & Security Defaults

**Tests**: Required; each phase's tests are written and observed failing first.

## Phase 1: Setup

- [X] T001 `crates/core/src/security/` module skeleton; `python/ferro/{middleware,security}.py` skeletons; export `CORS`

## Phase 2: Foundational (policies in Rust)

- [X] T002 [P] Tests `crates/core/tests/security.rs`: origin check table (methods, Sec-Fetch-Site values, Origin same/other/null/trusted, missing headers), CORS decisions (preflight allowed/denied, wildcard, credentials, vary), token buckets (refill, retry-after, independence, eviction), proxy resolution (CIDR, chain, untrusted peer), cookie header parsing, rate-limit spec parsing
- [X] T003 Implement `security::{origin,cors,ratelimit,proxy,cookies}` and settings validation
- [X] T004 Route options in the router/server table (`rate_limit`, `csrf`) and settings plumbing Rust ↔ Python (`config.py`, `server.rs`)

**Checkpoint**: `cargo test --workspace` green; existing Python tests green

## Phase 3: US1 - Middleware (P1) 🎯

- [X] T005 [US1] Tests `tests/python/test_middleware.py`: order, short-circuit, result kinds → Response, header edits, exceptions through call_next, request.state, double call_next, non-Response return, no-middleware path unchanged
- [X] T006 [US1] `python/ferro/middleware.py` composition + `App.middleware`; `Response` header mutability; native `request.state`

## Phase 4: US2 - Browser security (P1)

- [X] T007 [US2] Tests `tests/python/test_security.py`: CSRF scenarios (incl. trusted origins, `csrf=False`, GET never blocked, handler counter stays 0), CORS preflight/actual responses, startup refusal for wildcard+credentials, default/opt-in headers and overrides, cookies parse/set/delete/validation and Secure by mode
- [X] T008 [US2] Apply CSRF/CORS/headers in `crates/http/src/service.rs`; `request.cookies`; `Response.set_cookie/delete_cookie`; `ferro.CORS`

## Phase 5: US3 - Rate limiting (P2)

- [X] T009 [US3] Tests `tests/python/test_rate_limit.py`: global and per-route limits, 429 + retry-after, per-client independence (trusted proxy header to simulate clients), recovery, invalid specs
- [X] T010 [US3] Limiter in the service; client address resolution; `request.client`; access log client

## Phase 6: Polish

- [X] T011 [P] Docs (getting-started security & middleware section, README, roadmap)
- [X] T012 `tests/benchmarks/run_006.py`: SC-002 hello world with/without middleware vs Feature 001, SC-004 limiter with 1,000 client addresses
- [X] T013 Gates + quickstart validation; notes

## Implementation Notes (recorded during /speckit-implement)

- **Test-first observance.** T002 was written before the policy code and observed failing (the
  `security` module did not exist); the policy implementation was then written in one pass and
  all policy tests passed. The Python story tests (T005, T007, T009) were written first and
  observed failing (missing `CORS`, `App.middleware`, `rate_limit=`), then passed after T006,
  T008 and T010 without further changes.
- **Found while benchmarking:** the limiter only swept idle buckets when new clients arrived, so
  after a burst of 100,000 addresses followed by quiet traffic 94,735 buckets stayed in memory.
  Shards now also sweep at least once a minute (test `memory_is_returned_after_a_burst_of_clients`);
  one hour after the burst 1,000 buckets remain.
- **Contract changes:** the native settings dict carries the new security keys
  (`test_native_settings_shape` updated); every response gains `x-frame-options: DENY` and
  `cross-origin-opener-policy: same-origin` unless the handler sets them.
- **Design notes:** CSRF uses fetch metadata and `Origin` instead of tokens; Python middleware runs
  only for routed requests; the cookie `Secure` default follows the most recently started server
  in the process (one app per process is the supported setup).
- **Results** (`tests/benchmarks/results/006-middleware-security.md`): SC-002 — hello world without middleware 15,887 req/s (median of 3 alternating runs), 84% of the Rust-only baseline measured in the same session (Feature 001: 76%); raw throughput is below Feature 001's 18,886 req/s because the machine itself was slower (Rust baseline 18,916 vs 24,800 req/s then); one pass-through middleware stays within run-to-run noise (+5%); 0 failed requests. A first run made while other tests were using the CPU was discarded. SC-003 — refused requests never reach Python (handler counters in tests). SC-004 — 136 ns per limiter check over 1,000 addresses; memory returns to 1,000 buckets after a 100,000-address burst; a never-exceeded rate limit costs about 3% throughput (within noise). Quickstart validated against `ferro dev` (CSRF 403, CORS preflight 204, headers, 429 after the route limit).
- **Gates:** fmt, clippy `-D warnings`, `cargo test` (150), ruff, `pytest` (576 passed, 2 skipped; Features 006 and 007 together) — Windows 11, CPython 3.13, PostgreSQL 18.1.
- **Deferred:** rate limits shared between processes (Feature 010), middleware for responses the
  engine answers itself.
