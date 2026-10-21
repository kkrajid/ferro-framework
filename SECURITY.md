# Security policy

## Supported versions

| Version | Security fixes |
|---|---|
| 0.6.x | yes |
| 0.5.x | until 0.6.0 is released |
| older | no (development versions, never published) |

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Use the repository's private
vulnerability reporting ("Security" tab → "Report a vulnerability") with:

- the affected version and component (engine, ORM, auth, admin, CLI, ...),
- steps or code to reproduce,
- the impact you expect.

You will get an answer within 7 days. Fixes are released as a patch version with a "Security"
entry in the [changelog](CHANGELOG.md); reporters are credited unless they prefer otherwise.

## Security model in short

- Request limits, timeouts, security headers, CSRF origin checks, CORS and rate limits are
  enforced in the Rust engine before Python runs.
- SQL is built only by the ORM compiler with bound parameters.
- Passwords use Argon2id; sessions are random 256-bit keys stored server-side in the database.
- The admin escapes every value and sends a strict Content-Security-Policy.
- The last review is in [docs/security-audit.md](docs/security-audit.md).
