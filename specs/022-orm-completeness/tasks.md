---
description: "Task list for Feature 022 — ORM Completeness"
---

# Tasks: ORM Completeness

**Input**: [spec.md](spec.md).

## Phase 1: Lookups and transforms

- [X] T001 `Lookup` gains `istartswith`, `iendswith`, `range`, `regex`, `iregex`, `has_key`, `has_keys`, `has_any_keys`; `check_lookup` per kind (`crates/orm/src/lookup.rs`)
- [X] T002 `Transform::{Date, Json}`; `resolve_lookup_key` parses transforms after the field; `Condition.transform` (`path.rs`, `query.rs`)
- [X] T003 Dialects: `date_part` (UTC for date-times), `regex_lookup`, new text lookups; JSON values/documents/key tests with keys as parameters (`compile.rs`)
- [X] T004 SQLite connections register `regexp()` and `ferro_json_contains()` (`crates/sqlite/src/functions.rs`, `regex` crate)
- [X] T005 `Operand::Subquery` for `__in=<queryset>`; `Query.distinct`, also in `count()`/`exists()`

## Phase 2: Expressions

- [X] T006 `Expr` gains literals, functions, `Case`, `OuterRef`, `Subquery`, `Exists`; `AnnotationValue::{Aggregate, Expr}`; `Query::annotate_expr`, `resolve_outer`, `unresolved_outer`
- [X] T007 Compiler writes expression annotations (typed literal casts on PostgreSQL, subqueries with the outer scope)
- [X] T008 Bridge converts expression trees; `PyQuery.annotate_expr`, `distinct`, `explain`; OuterRef outside a subquery is refused
- [X] T009 Python: `Value`, functions, `Case`/`When`, `Subquery`, `Exists`, `OuterRef`, `F % n`, `Q ^ Q`; `annotate()` mixes aggregates and expressions

## Phase 3: Helpers, managers, raw SQL

- [X] T010 `distinct`, `none`, `get_or_create`, `update_or_create`, `in_bulk`, `explain`, `raw`
- [X] T011 Reverse foreign-key `add`/`remove`/`clear`/`set`
- [X] T012 `raw_execute`/`raw_fetch` (`%s` → placeholders, typed parameters, model instances by column name); `db.execute`, `db.fetch`

## Phase 4: Tests and docs

- [X] T013 `tests/python/orm/test_orm_completeness.py` (9 tests × SQLite/PostgreSQL), public API list, `_ferro.pyi`
- [X] T014 Models guide (lookups, JSON, expressions, raw SQL, managers), security guide, CHANGELOG

## Implementation Notes

- **Found while testing**: PostgreSQL's `EXTRACT` reads `timestamptz` in the session's time
  zone, which on the test machine was not UTC; date parts of date-times now use
  `AT TIME ZONE 'UTC'`.
- **Not done**: `values()` after a grouped `annotate()` (so a `Subquery` cannot select an
  aggregate such as a per-row count; per-record `annotate(n=Count("items"))` covers the common
  case); `OuterRef` to the same model; `F()` across relations; `only()`/`defer()`;
  `union()`/`intersection()`/`difference()`; expressions in `update()`; `delete()` returning
  per-model counts.
- **Semantics to know**: SQLite's `Greatest`/`Least` return NULL when any argument is NULL;
  `__regex` follows each database's regular-expression syntax; JSON number comparison on
  SQLite follows SQLite's numeric comparison, on PostgreSQL `jsonb` ordering.
