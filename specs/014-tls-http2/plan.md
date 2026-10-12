# Implementation Plan: TLS and HTTP/2

**Branch**: `014-tls-http2` | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/014-tls-http2/spec.md`

## Summary

Two settings, `tls_cert` and `tls_key`, make the engine's listener speak TLS 1.2/1.3 (rustls with
the ring provider, already in the dependency tree) and offer HTTP/2 through ALPN. Files are read
and checked before the server listens; errors name the setting and the file. Requests on a TLS
listener are `https` for origin checks; HTTP/2 requests get their host from the authority
([research.md](research.md)).

## Technical Context

**Language/Version**: Rust 1.85+ (edition 2024); Python ≥ 3.12
**Primary Dependencies**: engine feature `rustls-0_23` (adds `actix-tls`, `h2`); `rustls`,
`rustls-pki-types` (workspace, already used by PostgreSQL); dev: `rcgen` (Rust), `trustme`, `h2`
(Python)
**Storage**: n/a
**Testing**: `crates/http/tests/tls.rs`; `tests/integration/test_tls.py`; config tests
**Target Platform**: Windows 11 (development), Linux/macOS (CI)
**Project Type**: framework (Rust engine + Python package)
**Performance Goals**: plain-HTTP path unchanged (one boolean per request for the scheme)
**Constraints**: engine types stay inside `ferro-http` (constitution II); no C toolchain
**Scale/Scope**: 7 functional requirements, 2 user stories

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| I Thin Python, Rust core | PASS | TLS, ALPN, HTTP/2 and validation in Rust; Python only resolves two settings and calls the check before binding in supervisor mode. |
| II Engine encapsulation | PASS | rustls/actix types confined to `ferro-http` (`tls.rs`); Python sees paths and `ValueError`. |
| III Framework-owned ORM | N/A | |
| IV Safe SQL | N/A | |
| V Secure by default | PASS | TLS 1.2+ only, safe defaults, handshake timeout, key material never logged, `https` scheme for origin checks; TLS remains opt-in (needs certificates). |
| VI Coarse boundary, non-blocking | PASS | No new Python crossings; HTTP/2 streams dispatch like HTTP/1.1 requests. |
| VII Test-first + backend parity | PASS | Rust and Python tests written first; no database behaviour changes. |
| VIII Measured performance | PASS | Plain path unchanged; TLS cost is the operator's choice (not benchmarked as a gate). |
| IX Django-familiar | PASS | Familiar `--certfile/--keyfile`-style options (`--tls-cert`, `--tls-key`), env vars. |
| X Scope discipline | PASS | Roadmap 014; ACME, mTLS, OCSP, hot reload and h2c deferred. |

## Project Structure

```text
Cargo.toml (rcgen workspace dev dependency)
crates/core/src/settings.rs (tls_cert, tls_key, validate_tls, tls())
crates/http/{Cargo.toml, src/tls.rs, src/server.rs, src/service.rs, src/lib.rs (check_tls), tests/tls.rs}
crates/python-bridge/src/{server.rs (settings keys, check in Server::new, check_tls), lib.rs}
python/ferro/{config.py, cli.py, app.py, server.py, supervisor.py, _ferro.pyi}
tests/integration/test_tls.py, tests/python/test_config.py
pyproject.toml, .github/workflows/ci.yml (trustme, h2)
docs/getting-started.md
```

## Complexity Tracking

None.
