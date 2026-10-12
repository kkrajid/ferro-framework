---
description: "Task list for Feature 014 — TLS and HTTP/2"
---

# Tasks: TLS and HTTP/2

**Input**: Design documents from `specs/014-tls-http2/` (plan, spec, research, contracts,
quickstart)

**Tests**: Required (constitution VII): written and observed failing before the implementation.

**Format**: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [X] T001 Engine feature `rustls-0_23` and `rustls`/`rustls-pki-types` in `crates/http/Cargo.toml`; `rcgen` (ring, no aws-lc) and `tempfile` as dev-dependencies; `trustme` and `h2` in the Python dev group and CI (`pyproject.toml`, `.github/workflows/ci.yml`)

## Phase 2: Foundational (settings)

- [X] T002 [P] Tests `tests/python/test_config.py`: `tls_cert`/`tls_key` precedence, pairs, `scheme`, native shape, CLI options
- [X] T003 `tls_cert`/`tls_key` in `crates/core/src/settings.rs` (fields, both-or-none validation, `tls()`), `crates/python-bridge/src/server.rs` (dict keys), `python/ferro/config.py` (specs, `Settings.scheme`, `_check_tls`), `python/ferro/cli.py` (`--tls-cert`, `--tls-key`), `python/ferro/app.py` (kwargs)

## Phase 3: User Story 1 — HTTPS (P1) 🎯 MVP

- [X] T004 [P] [US1] Rust tests `crates/http/tests/tls.rs`: HTTPS GET and probe, same-origin `https` POST allowed / `http` origin refused, plain HTTP on the TLS port fails alone, every misconfiguration reported by `check_tls` and `start` alike without key material
- [X] T005 [US1] `crates/http/src/tls.rs`: PEM loading, ring provider, safe protocol versions, error messages per `contracts/settings.md`; `check_tls` in `crates/http/src/lib.rs`
- [X] T006 [US1] `start_on` builds the TLS config before spawning, `listen_rustls_0_23` when TLS is on, `tls_handshake_timeout(header_timeout)` (`crates/http/src/server.rs`); `State.tls` → scheme `https` in the origin check (`crates/http/src/service.rs`)
- [X] T007 [US1] Early checks: `check_tls` in the native `Server` constructor and `_ferro.check_tls` (supervisor, before binding); `https://` banners (`python/ferro/server.py`, `python/ferro/supervisor.py`, `python/ferro/cli.py`)

## Phase 4: User Story 2 — HTTP/2 (P2)

- [X] T008 [P] [US2] Rust test: ALPN negotiates `h2` when offered (`crates/http/tests/tls.rs`)
- [X] T009 [P] [US2] Integration tests `tests/integration/test_tls.py`: HTTP/2 and HTTP/1.1 over TLS with 1 and 2 processes (host from authority, JSON body, 413, 404, readiness, 50 multiplexed requests, plain HTTP refused, `https://` banner); misconfiguration exits before serving without traceback or key text
- [X] T010 [US2] Host from the authority for the origin check and for handlers (`host()` and `request_headers()` in `crates/http/src/service.rs`)

## Phase 5: Polish

- [X] T011 Documentation: `docs/getting-started.md` settings table, "HTTPS and HTTP/2 without a proxy", limits; roadmap row 014 Implemented
- [X] T012 Gates: fmt, clippy, cargo test, ruff, full pytest

## Implementation Notes

- **Dependencies**: the engine's `rustls-0_23` feature pulls `actix-tls` and `tokio-rustls`
  without default features, so no aws-lc/C toolchain is needed; the ring provider is passed
  explicitly. The workspace's `rust-version` (1.85) made cargo pick `actix-tls 3.5.0` and
  `rcgen 0.14.7` (newer patch releases need Rust 1.88).
- **Validation in three places**: the native `Server` constructor (so `ferro run` exits 2 before
  binding), `start_on` (the configuration actually used) and `_ferro.check_tls` in the
  supervisor before the shared socket is bound. Tests compare `check_tls` and `start` messages
  and check that no PEM text leaks.
- **HTTP/2 and httpx**: the server multiplexes (20 streams of 200 ms finished in 0.26 s with a
  raw `h2` client), but httpx's synchronous HTTP/2 client handles one response at a time across
  threads and answers server PINGs late, so the engine's keep-alive ping closed its connection
  after ~13 s in an early test. SC-002 is therefore measured with a raw `h2` client
  (`multiplexed()` in `tests/integration/test_tls.py`: 50 × 0.2 s streams in well under 5 s).
- **413 over HTTP/2**: a client that uploads the whole body before reading the response (httpx)
  waits for flow-control credit the server never grants once it has answered 413; browsers and
  curl read the early response. The test uses a body above `max_body_size` but within the
  stream window. Documented as expected HTTP/2 behaviour.
- **Host on HTTP/2**: requests carry `:authority` instead of `Host`; the origin check and the
  headers given to Python fall back to the authority (tested: `request.headers["host"]` is
  `localhost:<port>` over both protocols).
- **Gates**: `cargo test` (5 TLS tests), `tests/integration/test_tls.py` (HTTP/1.1 and HTTP/2 with
  1 and 2 processes; misconfiguration), full suite green (739 passed, 3 skipped at the 013+014
  checkpoint).
