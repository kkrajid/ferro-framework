# Implementation Plan: Shared State Across Processes and a Cache API

**Branch**: `015-shared-state-cache` | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/015-shared-state-cache/spec.md`

## Summary

The multi-process supervisor creates a private directory holding a memory-mapped rate table
(new crate `ferro-shared`: lock-free GCRA over atomic words, keyed with a per-run secret) and a
metrics snapshot folder. Workers enforce rate limits through the shared table (falling back to
their own limiter when it is full) and publish their metrics text every second; `/metrics`
merges the live text with the other workers' snapshots. `ferro.cache` adds an async cache API
with a per-process memory backend and a database backend shared by every process
([research.md](research.md)).

## Technical Context

**Language/Version**: Rust 1.85+ (edition 2024); Python ≥ 3.12
**Primary Dependencies**: `memmap2` (new, in `ferro-shared` only)
**Storage**: shared memory file (rates), snapshot files (metrics), `ferro_cache` table (cache)
**Testing**: `crates/shared/tests/rate.rs`, `crates/http/tests/metrics_merge.rs`,
`tests/python/test_cache.py` (SQLite + PostgreSQL), `tests/integration/test_shared_state.py`
(4 processes: limits, metrics)
**Target Platform**: Windows 11 (development), Linux/macOS (CI)
**Performance Goals**: one CAS per rate-limited request; one metrics render per second per
worker; single-process servers unchanged
**Constraints**: `unsafe` confined to `ferro-shared`; no locks held across processes
**Scale/Scope**: 7 functional requirements, 3 user stories

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| I Thin Python, Rust core | PASS | Rate table, snapshots and merging in Rust; the cache is a Python API over the ORM (database work stays in the core). |
| II Engine encapsulation | PASS | No engine types cross; shared state is Ferro's own. |
| III Framework-owned ORM | PASS | Database cache is an ordinary Ferro model. |
| IV Safe SQL | PASS | Cache uses the ORM only (bound values). |
| V Secure by default | PASS | Private temp dir (owner-only), per-run hash secret, JSON instead of pickle, table full → still limited (process fallback). |
| VI Coarse boundary, non-blocking | PASS | Rate check is one atomic operation; snapshots written by a background thread; cache awaits like the ORM. |
| VII Test-first + backend parity | PASS | Tests first; database cache on SQLite and PostgreSQL. |
| VIII Measured performance | PASS | Hot path adds one CAS only under `--processes N` with limits; single process unchanged. |
| IX Django-familiar | PASS | Django's cache method names and `get_or_set`. |
| X Scope discipline | PASS | Roadmap 015; Redis/memcached and cross-machine limits deferred. |

## Project Structure

```text
Cargo.toml (members + memmap2, ferro-shared)
crates/shared/{Cargo.toml, src/lib.rs, tests/rate.rs}
crates/core/{Cargo.toml, src/settings.rs (shared_state, metrics_dir), src/security/{mod.rs, ratelimit.rs}}
crates/http/src/{metrics.rs (snapshots, merge), server.rs (writer thread), service.rs}, tests/metrics_merge.rs
crates/python-bridge/src/{server.rs (keys, create_shared_state), lib.rs}
python/ferro/{cache/__init__.py, cache/models.py, config.py (cache), app.py, server.py, cli.py, supervisor.py, _ferro.pyi}
tests/python/test_cache.py, tests/integration/test_shared_state.py
docs/getting-started.md
```

## Complexity Tracking

| Violation | Why needed | Simpler alternative rejected because |
|---|---|---|
| `unsafe` code (constitution: safe Rust by default) | Viewing a shared mapping as atomics is the only way for processes to update one word without a round trip | IPC per request adds latency to every limited request; a database write per request is slower still |
