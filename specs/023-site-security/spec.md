# Feature Specification: Site Security Settings

**Feature Branch**: `023-site-security`

**Created**: 2026-10-20

**Status**: Implemented

**Input**: "https://docs.djangoproject.com/en/6.1/topics/security/ … read this; does my framework
need any update?" followed by "yes do".

## Clarifications

### Session 2026-10-20

- Q: Host header validation? → A: `allowed_hosts` (`example.com`, `.example.com` for the domain
  and its subdomains, `*`). Empty (the default) keeps today's behaviour — any host — and
  `ferro run` logs a warning when it serves other machines without the setting. Other hosts get
  400 before routing; probes and metrics are exempt so load balancers can use addresses.
- Q: HTTPS redirect? → A: `https_redirect`: requests whose scheme is `http` (from a trusted
  proxy's `X-Forwarded-Proto`, or a plain listener) get 308 to `https://<host><path>`; skipped
  when Ferro serves TLS itself. The host is validated first.
- Q: HSTS options? → A: `hsts_include_subdomains` (default on, the current header) and
  `hsts_preload` (needs subdomains and at least one year, else a configuration error).
- Q: Login brute force? → A: `authenticate()` counts failures per username, and per client
  address when the new optional `request` argument is given, in the configured cache; after
  `max_login_attempts` (5) it raises `LoginThrottled` (HTTP 429, `Retry-After`) for
  `login_lockout` seconds (900), even for the right password. Unknown usernames count too.
  Success resets the count. `app.add_auth(max_login_attempts=, login_lockout=)`; 0 disables.
  The admin login passes the request and shows the message.
- Q: CSP nonces, uploads? → A: Out of scope: Ferro has no template engine (CSP stays a fixed
  header) and no multipart uploads yet.

## Requirements *(mandatory)*

- **FR-001**: `allowed_hosts` with a startup warning when unset in production.
- **FR-002**: `https_redirect`.
- **FR-003**: HSTS `includeSubDomains` / `preload` options.
- **FR-004**: Login throttling.
- **FR-005**: Documentation (security guide, settings reference, authentication guide).

## Success Criteria *(mandatory)*

- **SC-001**: Tests over real HTTP for every requirement; the full suite passes.
- **SC-002**: Existing applications keep working unchanged (all new settings default to the
  previous behaviour, except the login throttle, which is announced in the CHANGELOG).
