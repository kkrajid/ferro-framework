# Feature Specification: Signals, on_commit and Lifecycle Hooks

**Feature Branch**: `024-signals-and-hooks`

**Created**: 2026-10-21

**Status**: Implemented

**Input**: "https://docs.djangoproject.com/en/6.1/topics/signals/ … read this; does my framework
need any update?" followed by "yes do".

## Clarifications

### Session 2026-10-21

- Q: Dispatch model? → A: `Signal.connect(receiver, sender=None, weak=True, dispatch_uid=None)`,
  `disconnect`, `@receiver(signal or list, **options)`; `await send()` / `await send_robust()`
  returning `[(receiver, result)]`. Receivers must accept `**kwargs`; sync and `async` receivers
  run one after another in connection order on the event loop (Django 6.1 runs async receivers
  concurrently; sequential order is simpler to reason about inside transactions).
- Q: Which built-in signals? → A: `pre_save`/`post_save`, `pre_delete`/`post_delete`,
  `m2m_changed`, `request_started`/`request_finished`/`got_request_exception`,
  `user_logged_in`/`user_logged_out`/`user_login_failed`, `post_migrate`. Not `pre_init`/
  `post_init` (a cost on every row), `class_prepared`, `setting_changed`, `connection_created`.
- Q: Bulk operations? → A: No signals for `bulk_create`, `bulk_update`, `QuerySet.update/
  delete` and database cascades, as in Django; documented.
- Q: Request signals cost? → A: Endpoints are wrapped only when request-signal receivers are
  connected before the server starts.
- Q: `on_commit`? → A: `await db.on_commit(callback, robust=False)`; runs after the outermost
  commit, dropped on rollback and with a rolled-back nested block, at once outside a
  transaction; callbacks run outside the finished transaction.
- Q: Lifecycle? → A: `@app.on_startup` (errors stop the start) and `@app.on_shutdown` (reverse
  order, errors logged), on the server's loop, in `TestClient` too.

## Requirements *(mandatory)*

- **FR-001**: `ferro.signals.Signal` and `receiver`.
- **FR-002**: Model, many-to-many, request, auth and migration signals.
- **FR-003**: `db.on_commit`.
- **FR-004**: `App.on_startup` / `App.on_shutdown`.
- **FR-005**: Documentation (new guide page).

## Success Criteria *(mandatory)*

- **SC-001**: Tests for each signal (model signals on both databases), on_commit semantics and
  hooks; the full suite passes.
- **SC-002**: Without receivers, saving and deleting cost one attribute check each.
