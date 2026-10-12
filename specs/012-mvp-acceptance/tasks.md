---
description: "Task list for Feature 012 — MVP Acceptance: E-commerce Application"
---

# Tasks: MVP Acceptance — E-commerce Application

**Tests**: Required; the acceptance test is written and observed failing first.

## Phase 1: Setup

- [X] T001 Scaffold `examples/ecommerce/` with `ferro new ecommerce`

## Phase 2: US1 - Build and run with Ferro's commands (P1) 🎯

- [X] T002 [US1] Acceptance test `tests/integration/test_ecommerce.py`: fresh `ferro new ecommerce` + the example's files → `makemigrations` (no changes), `migrate`, `createsuperuser`, `shell -c` seed, `dev`, readiness, clean stop — SQLite and PostgreSQL; the project's own tests
- [X] T003 [US1] Models in `examples/ecommerce/models.py`; migrations generated with `ferro makemigrations` (`migrations/0001_initial.py`)

## Phase 3: US2 - Customers shop through the API (P1)

- [X] T004 [US2] Journey in the acceptance test: register, login, me, browse, order, pay, stock, 401; 20 customers racing for 5 items
- [X] T005 [US2] API in `examples/ecommerce/app.py` (account, catalogue, orders, cancel, test payments; transactions and conditional stock updates)
- [X] T006 [P] [US2] The shop's own tests `examples/ecommerce/tests/test_shop.py`

## Phase 4: US3 - Staff in the admin (P2)

- [X] T007 [US3] Admin registrations `examples/ecommerce/admin.py`; admin login and model pages in the acceptance test

## Phase 5: Polish

- [X] T008 [P] Docs: `examples/ecommerce/README.md`, getting-started, README status, roadmap
- [X] T009 Gates and notes

## Implementation Notes (recorded during /speckit-implement)

- **Test-first observance.** The acceptance test (T002, T004, T007) was written before the
  migrations existed and observed failing (`makemigrations` created `0001_initial.py` instead of
  reporting no changes). The shop's own tests (T006) were written together with the shop and
  passed at once — they are the example's tests, not framework code.
- **One finding, behaviour confirmed rather than changed:** the first journey run against
  `ferro run` lost the session: production session cookies are `Secure`, so an HTTP (not HTTPS)
  client correctly keeps them to itself. The acceptance test uses `ferro dev`, the command the
  Definition of Done names; production cookie behaviour stays as designed (Features 006/007).
- **No framework changes were needed**: models with relations to `ferro.auth.User`, generated
  migrations on both databases, sessions, `@login_required`, `transaction()`, conditional
  `update(F(...))`, slicing for pagination, `Q` search, the admin and `ferro shell -c` all worked
  as built in Features 001–011.
- **Results**: SC-001–SC-004 met — the Definition of Done sequence runs unattended on SQLite and
  PostgreSQL; the journey succeeds on both; 20 concurrent customers bought exactly the 5 items in
  stock (15 × 409, stock 0) on both; the shop's 5 tests and the 3 acceptance tests pass.
- **Gates**: see Feature 011's notes (run together): ruff, full pytest, cargo clippy, cargo test.
