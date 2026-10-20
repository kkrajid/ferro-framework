---
description: "Task list for Feature 023 — Site Security Settings"
---

# Tasks: Site Security Settings

**Input**: [spec.md](spec.md).

- [X] T001 `security::hosts::AllowedHosts` (patterns, port and case handling, IPv6) with unit tests (`crates/core/src/security/hosts.rs`)
- [X] T002 Settings `allowed_hosts`, `https_redirect`, `hsts_include_subdomains`, `hsts_preload` (Rust `Settings`, bridge transfer, `config.py`, `App(...)`)
- [X] T003 `Policy::host_allowed`, `https_location`; HSTS header options and preload validation
- [X] T004 `site_check` in the HTTP service: 400 for foreign hosts, 308 to HTTPS; probes and metrics answered before it
- [X] T005 `ferro run` warns when `allowed_hosts` is empty on a non-loopback address outside debug
- [X] T006 `LoginThrottled`, `configure_login_throttle`, `authenticate(..., request)`; `app.add_auth(max_login_attempts=, login_lockout=)`; admin login shows the message
- [X] T007 Tests: `tests/python/test_site_security.py`, `tests/python/auth/test_login_throttle.py` (both databases), settings names, public API; autouse fixture keeps the throttle off for unrelated tests
- [X] T008 Security guide, settings reference, authentication guide, CHANGELOG

## Implementation Notes

- **Behaviour change**: the login throttle is on by default (5 failures / 15 minutes). The
  test suite needed an autouse fixture because tests in one process share the memory cache.
- **Known limits**: with the memory cache and `--processes N`, each process counts on its own
  (up to N × 5 attempts); without `request`, the count is per username, so a known username
  can be locked out by anyone for the lockout period (documented).
- **Not done**: CSP nonces (no template engine), upload hardening (no multipart support).
