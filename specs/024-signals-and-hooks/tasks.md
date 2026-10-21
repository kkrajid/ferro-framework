---
description: "Task list for Feature 024 — Signals, on_commit and Lifecycle Hooks"
---

# Tasks: Signals, on_commit and Lifecycle Hooks

**Input**: [spec.md](spec.md).

- [X] T001 `python/ferro/signals.py`: `Signal` (weak references, sender filter, `dispatch_uid`, `**kwargs` check, `send`, `send_robust`), `receiver`, built-in signals
- [X] T002 `Model.save()`/`delete()` send model signals (only when receivers exist); many-to-many managers send `m2m_changed` (`set()` removes through `remove()`)
- [X] T003 `login()`, `logout()`, `authenticate()` send auth signals; `migrate()` sends `post_migrate`
- [X] T004 `db.on_commit()`: a context-variable stack of callback lists per transaction block
- [X] T005 `App.on_startup`/`on_shutdown`; `ServerRunner` runs them on its loop; request signals wrap endpoints when connected
- [X] T006 Tests: `tests/python/orm/test_signals.py`, `tests/python/auth/test_auth_signals.py`, `tests/python/migrations/test_post_migrate.py`; public API
- [X] T007 `docs/guide/18-signals-and-hooks.md`, docs index, models guide link, CHANGELOG

## Implementation Notes

- **Order**: receivers run sequentially (sync and async alike) instead of Django 6.1's
  concurrent async receivers, so receivers inside one transaction never interleave.
- **Not done**: signals for bulk operations and cascades (none in Django either); `pre_init`/
  `post_init`; request signals connected after the server started are not wired.

## Verification pass (2026-10-21)

- Release wheel (`maturin build --release`) in its own venv: a smoke script over iterator,
  expressions, JSON lookups, raw SQL, isolation, `on_commit`, `post_save`, `allowed_hosts` and
  `on_startup` passed on SQLite and PostgreSQL; the e-commerce integration tests passed against
  the release build.
- `save()` overhead with one receiver is within the noise of a committed save
  ([results](../../tests/benchmarks/results/024-save-overhead.md)).
- Edge cases added and green on both databases: JSON negative indexes, nested `has_key`,
  booleans and floats, containment on nested values, Unicode regex, `%%s` in raw SQL,
  `on_commit` from child tasks, a failing receiver rolling back its transaction, IPv6 hosts,
  WebSocket handshakes with foreign hosts, no redirect under TLS, database-cache throttle
  shared by two servers, and migrations of indexes created before pattern indexes.
- Found and fixed: `TestClient.websocket_connect(headers={"host": ...})` sent two `Host`
  headers; the given one now replaces the address.
- Found and fixed (pre-existing since Feature 020): a stop request arriving while the server
  logged was swallowed by the logging handler (`_Shutdown` derived from `Exception`); 6 of 20
  probe runs hung, 0 of 40 after making it a `BaseException`
  (`tests/python/test_stop_signals.py`, red first).
