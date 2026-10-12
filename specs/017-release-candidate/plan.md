# Implementation Plan: v0.5 Release Candidate

**Branch**: `017-release-candidate` | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

## Summary

A code-level security review of the whole repository with every confirmed finding fixed behind a
regression test ([docs/security-audit.md](../../docs/security-audit.md)); the public API frozen for
0.5.x and pinned by tests ([docs/stability.md](../../docs/stability.md)); and a production
deployment guide with ready-to-edit files ([docs/deployment.md](../../docs/deployment.md),
[deploy/](../../deploy/)), checked against the real settings and commands by a test.

## Technical Context

**Language/Version**: Rust 1.85+, Python ≥ 3.12 · **Dependencies**: none new ·
**Testing**: cargo tests (core, http), pytest (auth, admin, public API, deployment files)

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| V Secure by default | PASS | The point of the feature: fixes tighten defaults (wildcard CORS, sessions, IPv6 keying, admin users) without new opt-ins. |
| VII Test-first | PASS | Each fix has a regression test (see tasks); behaviour changes are listed in the changelog. |
| IX Django-familiar | PASS | Session invalidation on password change, absolute login age and write-only admin passwords follow Django's behaviour. |
| X Scope discipline | PASS | Roadmap 017; external penetration testing out of scope. |

Other principles: unaffected (no new crossings, SQL or engine types).

## Behaviour changes (changelog "Security")

- Cross-origin unsafe requests **with cookies** are no longer let through by `CORS(allow_origins=["*"])`;
  list the origins (or use `trusted_origins`) for cookie-authenticated cross-origin posts.
- IPv6 clients share a rate-limit budget per /64.
- Logins end after 30 days and when the password changes (call `login(request, user)` again after
  a user changes their own password to stay signed in).
- The admin never shows user password hashes; only superusers change privileges.
