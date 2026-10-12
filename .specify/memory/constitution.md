<!--
Sync Impact Report (amendment 2026-10-10)
- Version change: 1.0.0 → 1.1.0 (MINOR: technology constraint materially changed)
- Modified sections: Technology & Platform Constraints → "Python bridge" now specifies a
  Ferro-owned asyncio bridge; pyo3-async-runtimes no longer mandated.
- Reason: specs/001-runtime-core/research.md R2 — pyo3-async-runtimes 0.29 does not cancel
  Python tasks when the Rust future is dropped, which breaks request timeouts and
  client-disconnect cancellation.
- Principles: unchanged. Templates: no edits required.
- Deferred TODOs: none

Sync Impact Report (initial)
- Version change: (template) → 1.0.0
- Modified principles: none (initial ratification)
- Added principles:
  I. Thin Python, Rust Core
  II. Engine Encapsulation
  III. Framework-Owned ORM
  IV. Safe SQL by Construction
  V. Secure by Default
  VI. Coarse Boundary, Non-Blocking Runtime
  VII. Test-First with Backend Parity (NON-NEGOTIABLE)
  VIII. Measured Performance
  IX. Django-Familiar Developer Experience
  X. Scope Discipline
- Added sections: Technology & Platform Constraints; Development Workflow & Quality Gates; Governance
- Removed sections: none
- Templates reviewed: plan-template.md (Constitution Check reads this file at runtime — ✅ no edit
  needed), spec-template.md ✅, tasks-template.md ✅, checklist-template.md ✅
- Deferred TODOs: none
-->

# Ferro Constitution

Ferro is a Python application framework with a Rust-native execution engine and ORM. This
constitution governs every specification, plan, task and line of code in this repository. The
product source of truth is [docs/product-vision.md](../../docs/product-vision.md).

## Core Principles

### I. Thin Python, Rust Core

- Python code in `python/ferro/` MUST be limited to developer-facing API: model declaration,
  route registration, handler invocation, configuration and ergonomics.
- HTTP serving, request parsing, routing, middleware execution, query building, SQL generation,
  connection pooling, transactions, serialization, schema/migration logic and security primitives
  MUST be implemented in Rust under `crates/`.
- A feature that places infrastructure logic in Python MUST justify it in the plan's Complexity
  Tracking table, including the plan to move it to Rust.

Rationale: the performance and stability promise of Ferro depends on Python being a thin
declarative layer over a Rust engine.

### II. Engine Encapsulation

- Actix Web, Tokio, PyO3, database driver crates and any other third-party engine types MUST NOT
  appear in the Python public API or in error messages shown to application developers.
- Each engine is wrapped behind a Ferro-owned Rust abstraction (request, response, router,
  middleware, backend adapter). Only the adapter crate for that engine may import it.
- Replacing the HTTP engine or a database driver MUST NOT require changes to any user application.

Rationale: Ferro must be able to swap Actix or a driver without breaking users.

### III. Framework-Owned ORM

- The ORM pipeline is Model metadata → Query AST → query planner → SQL compiler → backend adapter,
  all owned by Ferro.
- Ferro MUST NOT wrap or depend on Django ORM, SQLAlchemy, Tortoise, Diesel, SeaORM or any other
  ORM. Low-level drivers (connection, wire protocol) are permitted behind the backend adapter.
- The ORM MUST stay database-independent; backend specifics live only in backend adapters
  (placeholder style, type mapping, introspection, DDL dialect).

Rationale: the owned ORM and migration engine are Ferro's main technical differentiator.

### IV. Safe SQL by Construction

- All user-supplied values MUST reach the database as bound parameters. Building SQL by
  concatenating or formatting values is forbidden everywhere, including migrations and admin.
- Identifiers (table, column, index names) MUST come from validated model metadata and be quoted
  by the backend dialect.
- Every SQL compiler change MUST include tests asserting both the SQL text and the parameter list.

Rationale: SQL injection must be impossible through any public Ferro API.

### V. Secure by Default

- Default configuration MUST be the safe configuration: request size limits, timeouts, secure
  headers, secure cookies, CSRF protection for cookie-authenticated unsafe methods, strong password
  hashing, and no debug detail in production error responses.
- Weakening a protection MUST require an explicit, named setting; no protection may be disabled
  implicitly by another setting.
- Internal errors returned to clients in production mode MUST NOT include stack traces, SQL,
  file paths or engine type names.

Rationale: developers should have to opt out of security, never opt in.

### VI. Coarse Boundary, Non-Blocking Runtime

- A request MUST cross the Python↔Rust boundary a bounded, small number of times: Rust → Python
  handler → (Rust ORM/runtime calls that each do whole operations) → Rust response. Per-row,
  per-field or per-byte crossings in hot paths are forbidden.
- Large buffers (request bodies, result sets, serialized responses) MUST NOT be copied between
  Python and Rust more than once per direction.
- Actix workers and Tokio executor threads MUST NEVER block on database, file or network I/O or on
  long-running Python code. Blocking or CPU-heavy work runs on a dedicated execution path.
- Every queue between components MUST be bounded where backpressure is possible.

Rationale: boundary crossings and blocked workers are the two main ways a hybrid framework loses
its performance.

### VII. Test-First with Backend Parity (NON-NEGOTIABLE)

- Tests for a behavior MUST be written and observed failing before the implementation that makes
  them pass (red → green → refactor).
- Rust units are tested with `cargo test`; the Python API is tested with `pytest` against the
  built extension; end-to-end tests exercise real HTTP requests against a running Ferro server.
- Every ORM and migration test MUST run against every supported backend (SQLite and PostgreSQL in
  the MVP). A feature is not done if any backend fails.
- Bug fixes MUST start with a failing regression test.

Rationale: a framework other people build businesses on cannot rely on manual verification.

### VIII. Measured Performance

- Performance claims (in docs, README or release notes) MUST be backed by a reproducible benchmark
  in `benchmarks/` with stated hardware, method and baseline.
- The baseline is a raw Actix Web application doing the same work. Ferro's Rust-only route path
  MUST stay close to that baseline; the Python route path is measured and optimized separately.
- Each minor release MUST record benchmark results; a regression above 10% in any tracked scenario
  blocks release unless justified in the release notes.

Rationale: "fast" is a measurement, not a slogan.

### IX. Django-Familiar Developer Experience

- Public API names and shapes MUST follow Django/FastAPI conventions where a familiar equivalent
  exists (`Model`, `objects`, `filter(field__lookup=...)`, `@app.get("/path/{id}")`,
  `makemigrations`, `migrate`).
- A developer MUST be able to build and run an application without knowing Rust, Actix, Tokio or
  PyO3, and without installing a Rust toolchain when using published wheels.
- Every public API MUST ship with type hints (`.pyi` stubs for the native module), docstrings and
  at least one runnable example.
- Errors shown to developers MUST name the developer's code (route, model, field) and suggest a fix.

Rationale: adoption depends on Ferro feeling like a normal Python framework.

### X. Scope Discipline

- Work MUST map to a feature in [docs/roadmap.md](../../docs/roadmap.md) and a section of the
  product vision. Items listed under "Out of MVP Scope" MUST NOT be built before v1.0.
- Public API for a subsystem is frozen in its spec before advanced capabilities of that subsystem
  are implemented.
- YAGNI applies inside features: build the smallest design that satisfies the spec's acceptance
  scenarios; extension points are added when a second concrete use exists.

Rationale: the MVP is already large; scope creep is the main risk to shipping it.

## Technology & Platform Constraints

- **Rust:** stable toolchain, edition 2024, Cargo workspace rooted at `Cargo.toml` with crates in
  `crates/`. `unsafe` code is forbidden outside the Python bridge crate and MUST carry a
  `// SAFETY:` comment.
- **HTTP engine:** Actix Web 4.x on Tokio, confined to the HTTP adapter crate (Principle II).
- **Python bridge:** PyO3, with a Ferro-owned asyncio bridge that MUST cancel the Python task
  when the Rust side abandons a request (timeout, disconnect, shutdown). Helper crates such as
  `pyo3-async-runtimes` MAY be used only where they meet that requirement. Packaged with maturin
  as the `ferro` distribution, native module `ferro._ferro`.
- **Python:** CPython ≥ 3.12. Free-threaded builds are not targeted in the MVP.
- **Databases (MVP):** SQLite and PostgreSQL. MySQL/MariaDB are post-MVP.
- **Platforms:** Linux, macOS and Windows MUST build and pass tests; Linux is the primary
  production target.
- **Serialization:** JSON encoding/decoding of request and response bodies happens in Rust.
- **Logging:** structured logging through the `tracing` ecosystem; Python logs route through the
  same pipeline.

## Development Workflow & Quality Gates

- **Spec-driven flow:** every feature goes through `/speckit-specify` → `/speckit-clarify` →
  `/speckit-plan` → `/speckit-tasks` → `/speckit-analyze` → `/speckit-implement`. Artifacts live in
  `specs/NNN-feature-name/`.
- **Constitution Check:** every `plan.md` MUST evaluate each principle above as PASS, or record a
  justified violation in Complexity Tracking. `/speckit-analyze` MUST report zero CRITICAL issues
  before implementation starts.
- **Gates before merge:** `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`,
  `cargo test --workspace`, `ruff check`, `ruff format --check`, `pytest`, and the feature's
  end-to-end tests MUST all pass on CI.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `perf:`,
  `chore:`), one logical change per commit.
- **Public API changes** MUST update stubs, docs and examples in the same change.

## Governance

- This constitution supersedes conflicting conventions, plans and habits. When a plan conflicts
  with it, the plan changes or the constitution is formally amended first.
- **Amendments** are made with `/speckit-constitution`, include a Sync Impact Report, and are
  committed separately as `docs: amend constitution to vX.Y.Z (...)`.
- **Versioning:** MAJOR for removing or redefining a principle; MINOR for adding a principle or
  materially expanding guidance; PATCH for clarifications and wording.
- **Compliance review:** every plan's Constitution Check, every `/speckit-analyze` run and every
  code review verifies compliance. Unjustified complexity is rejected.

**Version**: 1.1.0 | **Ratified**: 2026-10-10 | **Last Amended**: 2026-10-10
