---
description: "Task list for Feature 008 — Admin"
---

# Tasks: Admin

**Tests**: Required; each phase's tests are written and observed failing first. Database tests
run on SQLite and PostgreSQL.

## Phase 1: Setup

- [X] T001 `python/ferro/admin/` package skeleton, stylesheet, `App.add_admin` stub

## Phase 2: Foundational

- [X] T002 [P] Tests for `request.form()` (urlencoded, repeated keys, `+`/percent decoding, wrong content type → 400) in `tests/python/test_forms.py`
- [X] T003 `request.form()` in `crates/python-bridge/src/request.rs`; HTML helper with escaping (`python/ferro/admin/html.py`), covered by the escaping tests in T004

## Phase 3: US1 - Browse (P1) 🎯

- [X] T004 [US1] Tests `tests/python/admin/test_pages.py`: index, list columns/links/total/pagination, search, filters, ordering, escaping of hostile values, query count for a list page
- [X] T005 [US1] `site.py` (register, routes), `options.py` (`ModelAdmin`), list/index views

## Phase 4: US2 - Edit (P1)

- [X] T006 [US2] Tests `tests/python/admin/test_forms.py`: widgets per kind, create/change/delete flows, validation messages with kept values, unique errors, readonly fields, FK select and M2M multi-select, protected delete, UUID primary keys, value round trips
- [X] T007 [US2] `forms.py` (widgets, parsing, validation), add/change/delete views

## Phase 5: US3 - Access (P1)

- [X] T008 [US3] Tests `tests/python/admin/test_access.py`: login redirect with safe `next`, staff-only, per-model permissions, cross-site posts refused, CSP/no-store headers, login rate limit
- [X] T009 [US3] Login/logout views, permission checks, headers

## Phase 6: Polish

- [X] T010 [P] Docs (getting-started admin section, README, roadmap)
- [X] T011 Gates + quickstart validation; notes

## Implementation Notes (recorded during /speckit-implement)

- **Test-first observance.** All story tests (T002, T004, T006, T008) were written before the
  admin package and observed failing (collection failed: `ferro.admin` had no `AdminSite`). The
  first run after implementing exposed one bug — the HTML helper's `tag(name, ...)` parameter
  collided with the `name=` attribute, so every form page answered 500; it is now
  positional-only. One test asserted an attribute order more strictly than intended and was
  satisfied by emitting `multiple` before `id`.
- **Design notes:** pages are built with a tiny escaping helper instead of a template engine;
  the stylesheet is inline and allowed by its SHA-256 in the Content-Security-Policy, so no
  static route or script is needed. Permissions for the four actions of a model are loaded in
  one query per request. `request.form()` parses urlencoded bodies in Rust (strict UTF-8).
- **Results:** SC-001 — 35 tests (pages, forms, access, form parsing) on SQLite and PostgreSQL;
  SC-002 — a list page over 10,003 rows takes a median of 12.9 ms (debug build, SQLite in
  memory) with at most 4 queries (test asserts the query count); SC-003 — hostile values in
  names, related labels and search terms are escaped (test).
- **Quickstart validated** against `ferro dev`: anonymous redirect, staff login, adding a
  category and a product through the forms, list page.
- **Gates:** fmt, clippy `-D warnings`, `cargo test` (150), ruff, `pytest` (611 passed,
  2 skipped) — Windows 11, CPython 3.13, PostgreSQL 18.1.
- **Deferred:** inline editing of related rows, bulk actions, change history, custom admin views.
