# Implementation Plan: Pydantic Integration

**Branch**: `019-pydantic-integration` | **Date**: 2026-10-12 | **Spec**: [spec.md](spec.md)

## Summary

Handler parameters annotated with Pydantic models receive the validated request body; return
annotations that use Pydantic models convert and filter results; Ferro's Rust JSON encoder writes
Pydantic models wherever JSON is produced. Pydantic stays optional and is never imported by
Ferro ([research.md](research.md)).

## Technical Context

**Language/Version**: Python ≥ 3.12; Rust 1.85+ (one fallback branch in the JSON encoder)
**Primary Dependencies**: Pydantic ≥ 2.7 (optional extra; dev/CI dependency)
**Testing**: `tests/python/test_pydantic.py` (bodies, errors, forms, sync handlers, registration
errors, returned models, response annotations with ORM objects and relations, sockets)
**Performance Goals**: no change for routes without Pydantic annotations (detection at
registration only; encoder check only on unknown types)
**Scale/Scope**: 7 functional requirements, 3 user stories

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| I Thin Python, Rust core | PASS | Validation and serialization run in Pydantic's Rust core; the encoder splices its JSON. Ferro adds only argument wiring. |
| II Engine encapsulation | PASS | No engine types involved. |
| V Secure by default | PASS | Response annotations send only declared fields; body size limits apply before validation. |
| VI Coarse boundary | PASS | One extra Python call per body/response on annotated routes only. |
| VII Test-first | PASS | `test_pydantic.py` written and run red before the implementation. |
| VIII Measured performance | PASS | Unannotated routes take the existing fast paths (unchanged code). |
| IX Django-familiar / ecosystem | PASS | FastAPI-style annotations familiar to Python API developers. |
| X Scope discipline | PASS | No Ferro serializer classes, no OpenAPI, no query models (spec Assumptions). |

## Project Structure

```text
crates/python-bridge/src/json.rs (Pydantic fallback)
python/ferro/routing.py (body parameters, response adapters), python/ferro/testing.py (`content=`)
pyproject.toml (extra `pydantic`, dev), .github/workflows/ci.yml (install pydantic)
tests/python/test_pydantic.py
docs/guide/03-routing-requests-responses.md (Pydantic section), docs/guide/10-testing.md, CHANGELOG.md, docs/stability.md, docs/roadmap.md
```
