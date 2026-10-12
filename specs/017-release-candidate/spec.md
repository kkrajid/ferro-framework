# Feature Specification: v0.5 Release Candidate

**Feature Branch**: `017-release-candidate`

**Created**: 2026-10-11

**Status**: Implemented

**Input**: User description: "v0.5 release candidate items: security audit, API freeze,
production deployment guide."

## Clarifications

### Session 2026-10-11

- Q: Scope of the security audit? → A: A code review of every component against the threats in
  the product vision (§15): injection (SQL, headers, HTML), CSRF, sessions and passwords, open
  redirects, proxies and client addresses, limits and denial of service, information leaks, admin
  authorization, unsafe Rust, secrets. Confirmed issues are fixed test-first; the report lists
  what was checked.
- Q: What does "API freeze" mean before 1.0? → A: The names in each public module's `__all__`,
  the CLI, settings, HTTP behaviour and migration format are frozen for 0.5.x; changes in 0.6+
  need a deprecation release first (`docs/stability.md`).
- Q: Deployment targets? → A: A single server with systemd (+ Caddy/nginx or Ferro's own TLS),
  Docker/Compose, Kubernetes, and platforms as a service, with ready-to-edit files in `deploy/`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Security review (Priority: P1)

An operator wants evidence that the release was reviewed for common web vulnerabilities.

**Acceptance Scenarios**:

1. **Given** the audit report (`docs/security-audit.md`), **Then** every finding has a severity,
   location, fix and test, and every checked area is listed.
2. **Given** a confirmed finding, **Then** a regression test fails before the fix and passes after.

### User Story 2 - API freeze (Priority: P1)

A developer upgrading within 0.5.x must not have to change code.

**Acceptance Scenarios**:

1. **Given** `docs/stability.md`, **Then** public names, CLI, settings and HTTP behaviour are
   listed, with versioning and deprecation rules.
2. **Given** the test suite, **Then** a test pins every public module's `__all__` so an
   accidental change fails CI.

### User Story 3 - Production deployment guide (Priority: P1)

An operator deploys a Ferro project to a server, Docker, Kubernetes or a PaaS following one guide.

**Acceptance Scenarios**:

1. **Given** `docs/deployment.md`, **Then** it covers the checklist, each target, migrations,
   HTTPS, proxies, probes, zero-downtime restarts, scaling, backups and monitoring.
2. **Given** `deploy/` files, **Then** each is referenced by the guide and consistent with
   Ferro's settings (names, probe paths, signals, timeouts).

## Requirements *(mandatory)*

- **FR-001**: `docs/security-audit.md` with findings, fixes and checked areas; fixes with tests.
- **FR-002**: `SECURITY.md` (how to report vulnerabilities).
- **FR-003**: `docs/stability.md` and a public-API test (`tests/python/test_public_api.py`).
- **FR-004**: `docs/deployment.md` and `deploy/` (Dockerfile, docker-compose.yml, Caddyfile,
  nginx.conf, ferro.service, kubernetes.yaml).
- **FR-005**: README links the guide, the policy and the changelog; status says release candidate.

## Success Criteria *(mandatory)*

- **SC-001**: No open critical or high findings at release.
- **SC-002**: Every public module's `__all__` is pinned by a test.
- **SC-003**: Every command and setting named in the deployment files exists (checked by a test).

## Assumptions

- An external penetration test is outside this feature; the audit is a code review.
