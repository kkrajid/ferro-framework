# Implementation Plan: Authentication

**Branch**: `007-authentication` | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

## Summary

`ferro.auth` provides Django-like users, groups, permissions and database sessions on Ferro's
ORM, an auth middleware setting `request.user`/`request.session`, decorators, and
`ferro createsuperuser`. Password hashing (Argon2id) runs in Rust on a dedicated thread pool
([research.md](research.md)).

## Technical Context

**Language/Version**: Rust 1.99; Python ≥ 3.12 · **Dependencies**: `argon2` (RustCrypto, pure
Rust) in `ferro-core`
**Testing**: `cargo test` (hash/verify/rehash/unknown formats), pytest on both databases (users,
sessions over HTTP with the `serve` fixture, permissions, decorators, CLI), latency check for
SC-002, timing check for SC-004

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| I | PASS | Hashing in Rust; auth logic is thin Python over the ORM. |
| II | PASS | No engine types. |
| III | PASS | Auth models are ordinary Ferro models. |
| IV | PASS | ORM only; no raw SQL. |
| V | PASS | Argon2id, hashed session keys, rotation, secure cookies, timing equalization. |
| VI | PASS | Hashing off the loop; one query per authenticated request. |
| VII | PASS | Tests first, both databases. |
| VIII | PASS | SC-002/SC-004 measured in tests. |
| IX | PASS | Django names: `authenticate`, `login`, `logout`, `login_required`, `has_perm`. |
| X | PASS | Roadmap 007; `createsuperuser` pulled forward from 009 (admin needs it). |

## Project Structure

```text
crates/core/src/passwords.rs                Argon2id hash/verify/needs_rehash (new)
crates/python-bridge/src/passwords.rs       pool + asyncio futures (new)
crates/python-bridge/src/request.rs         request.user / request.session getters
python/ferro/auth/{__init__,models,hashers,sessions,middleware,decorators,permissions,cli}.py
python/ferro/app.py                         App.add_auth()
python/ferro/cli.py                         createsuperuser; migrate → create_permissions
tests/python/auth/test_{users,sessions,permissions,cli}.py
```
