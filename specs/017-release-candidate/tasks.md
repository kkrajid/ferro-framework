---
description: "Task list for Feature 017 — v0.5 Release Candidate"
---

# Tasks: v0.5 Release Candidate

**Input**: `specs/017-release-candidate/spec.md`

**Tests**: every confirmed finding gets a regression test; the API freeze and the deployment
files are pinned by tests.

## Phase 1: User Story 1 — Security review (P1)

- [X] T001 Review of every component (read-only review with file:line evidence; findings in `docs/security-audit.md`)
- [X] T002 [P] Regression tests: `crates/core/tests/security.rs` (IPv6 /64 budgets, bucket cap, wildcard CORS with cookies), `crates/http/tests/security_review.rs` (forwarded-for lines, backslash redirect), `tests/python/auth/test_security_review.py` (session storage, password change, login age, purge, hashing flood), `tests/python/admin/test_user_admin.py` (password field, privilege fields)
- [X] T003 Fixes: `crates/http/src/service.rs` (`all_values`, redirect, cookies to the CSRF check), `crates/core/src/security/{mod.rs, cors.rs, ratelimit.rs}`, `crates/python-bridge/src/{passwords.rs, server.rs}`, `crates/shared/src/lib.rs` (0600), `python/ferro/auth/sessions.py`, `python/ferro/admin/{forms.py, site.py}`, `python/ferro/project.py` (`.env` 0600)
- [X] T004 `docs/security-audit.md` and `SECURITY.md`

## Phase 2: User Story 2 — API freeze (P1)

- [X] T005 `docs/stability.md` (public names, CLI, settings, HTTP behaviour, versioning, deprecation, supported versions)
- [X] T006 `tests/python/test_public_api.py`: every public module's `__all__` pinned; the policy names every module

## Phase 3: User Story 3 — Deployment guide (P1)

- [X] T007 `docs/deployment.md` (checklist, systemd, own TLS, Docker/Compose, Kubernetes, PaaS table, operating)
- [X] T008 [P] `deploy/`: `Dockerfile`, `docker-compose.yml`, `Caddyfile`, `nginx.conf`, `ferro.service`, `kubernetes.yaml`
- [X] T009 Test that every `FERRO_*` variable, `ferro` command and `ferro run` option in the guide and `deploy/` exists (`tests/python/test_public_api.py`)

## Phase 4: Polish

- [X] T010 README (status, links), roadmap rows 013–017, changelog
- [X] T011 Gates: fmt, clippy, cargo test, ruff, full pytest; release wheel smoke test

## Implementation Notes

- **Review**: done by a read-only review pass over every crate and Python module with file:line
  evidence; each finding was re-checked against the code before fixing. No critical/high.
- **Test-first caveat**: regression tests were written together with the fixes rather than run
  red against a separate build first (each build takes ~10 minutes on this machine). Each test
  targets the exact behaviour the finding describes (e.g. the forwarded-for test returns the
  client-supplied address without `all_values`).
- **Behaviour changes** are listed under "Security" in `CHANGELOG.md`; the stricter wildcard-CORS
  rule only affects cross-origin requests that carry cookies.
- **Finding 9** (PostgreSQL `sslmode=prefer` default) is accepted and documented rather than
  changed, to keep local and private-network databases working.
- **API freeze**: `tests/python/test_public_api.py` pins `__all__` of 8 modules and checks every
  `FERRO_*` variable, `ferro` command and `ferro run` option named in `docs/deployment.md` and
  `deploy/`. Checking the CLI while writing the policy corrected two command names
  (`ferro migration status`, `ferro db create-tables`).
- **Final gates (013–017 together)**: `cargo fmt` clean; `cargo clippy --workspace --all-targets
  -D warnings` clean (after renaming two hex literals in `ferro-shared`); `cargo test --workspace`
  all passing; `ruff` clean; `pytest`: 754 passed, 3 skipped, 1 failed —
  `test_unknown_users_take_as_long_as_wrong_passwords[postgres]` (a timing ratio, 0.22 s vs
  0.12 s while another project's server was running on the machine). It passed 6 of 6 reruns,
  alone and in suite order after the hashing-flood test; recorded as a load-sensitive test, not
  a regression.
