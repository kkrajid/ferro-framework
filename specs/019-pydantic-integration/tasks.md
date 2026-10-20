---
description: "Task list for Feature 019 — Pydantic Integration"
---

# Tasks: Pydantic Integration

**Input**: Design documents from `specs/019-pydantic-integration/` (plan, spec, research,
contracts, quickstart)

**Tests**: Required (constitution VII), written and run red before the implementation.

**Format**: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [X] T001 Pydantic 2 in the dev environment, the dev dependency group and CI (`pyproject.toml`, `.github/workflows/ci.yml`); optional extra `pydantic`

## Phase 2: Tests first

- [X] T002 [P] `tests/python/test_pydantic.py` written and run against the unchanged code: 8 of 8 failing (bodies, every problem in one 422, missing/optional/malformed/unsupported bodies, form bodies and sync handlers, registration errors, returned models, response annotations with ORM objects and relations, models in sockets and `json_dumps`)

## Phase 3: User Story 1 — Validated request bodies (P1) 🎯 MVP

- [X] T003 [US1] `routing.py`: Pydantic detection without importing it, `ParamSpec.model`, body parameter rules (one per handler, none on WebSockets or in the path), `_body_reader` (JSON from bytes, forms, 400/415/422), `_body_endpoint` merging path/query/body problems, async and sync handlers
- [X] T004 [US1] `TestClient` `content=` for raw bodies (`python/ferro/testing.py`)

## Phase 4: User Story 2 — Responses (P1)

- [X] T005 [US2] Return-annotation response models: `TypeAdapter` built at registration (`Response` removed from unions), `from_attributes` validation, `dump_json`, `None`/`Response` pass-through, `TypeError` naming handler and annotation (`routing.py`)
- [X] T006 [US2] Related managers iterable when prefetched (`python/ferro/models/relations.py`); unloaded-relation hints added to response errors; tests for reverse foreign keys and many-to-many

## Phase 5: User Story 3 — Models wherever JSON is written (P2)

- [X] T007 [US3] Rust JSON encoder fallback: objects with `__pydantic_serializer__` are written with `model_dump_json()` (`crates/python-bridge/src/json.rs`)

## Phase 6: Polish

- [X] T008 `docs/guide/17-pydantic.md` (examples run as a test), index, README, routing/testing/FAQ pages, stability policy, changelog, roadmap row 019
- [X] T009 Gates (shared with Feature 018): fmt, clippy, cargo test, ruff, full pytest

## Implementation Notes

- **Test-first**: all 8 initial tests failed before the implementation and passed after it
  without changes to the tests. Running the guide's examples then found a gap the tests had not
  covered — nested `list[...]` fields read from related managers (`product.tags`) failed with
  "Input should be a valid list" — fixed by making managers iterable over prefetched objects,
  with a new test (`test_many_to_many_lists_read_prefetched_objects`) and a reverse-foreign-key
  case in `test_return_annotations_convert_orm_objects`. Pydantic swallows exceptions raised by
  `__iter__`, so the "not loaded" explanation is added to the response error by Ferro
  (`_relation_hints`).
- **Form bodies**: the first test draft expected a single form value for a `list` field to be
  rejected; that is poor behaviour for HTML forms (one checked box), so fields typed as
  sequences always take every value of their key and the test was changed before implementing.
- **Performance**: routes without Pydantic annotations take the unchanged code paths (the
  fast paths of `_call_endpoint` and the encoder's known types); detection happens once at
  registration. No benchmark run was needed for SC-002 beyond this structural argument; the
  Pydantic validation cost itself is Pydantic's (Rust core).
- **Distribution name**: `ferro` on PyPI belongs to an unrelated project (checked 2026-10-12);
  at the maintainer's request the distribution was renamed `ferro-framework` (`pyproject.toml`,
  `ferro new` template, example project, release workflow, docs). Import name and command stay
  `ferro`; the extra is `ferro-framework[pydantic]`.
