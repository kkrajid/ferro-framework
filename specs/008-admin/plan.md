# Implementation Plan: Admin

**Branch**: `008-admin` | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

## Summary

`ferro.admin` registers models on an admin site and mounts server-rendered, escaped HTML pages
(index, list, add, change, delete, login) as ordinary routes on the app. Data access uses the ORM
(queries, relations, transactions); access control uses `ferro.auth` (staff + per-model
permissions); browser protection comes from Feature 006. `request.form()` is added to the native
request ([research.md](research.md)).

## Technical Context

**Language/Version**: Python ≥ 3.12 (admin), Rust 1.99 (form parsing) · **Dependencies**: none
new · **Testing**: pytest over HTTP with the `serve` fixture on both databases (pages, forms,
validation, permissions, escaping, query counts), Rust unit test for form parsing

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| I | PASS | Body parsing in Rust; admin pages are thin Python over the ORM. |
| II–IV | PASS | No engine types; ORM only; bound parameters. |
| V | PASS | Staff-only, permissions, escaped output, strict CSP, no-store, rate-limited login. |
| VI | PASS | One page query + count; `select_related` for list columns. |
| VII | PASS | Tests first, both databases. |
| VIII | PASS | SC-002 query count and time measured in tests. |
| IX | PASS | Django's `admin.register` / `ModelAdmin` options. |
| X | PASS | Roadmap 008; inlines, actions, history out of scope. |

## Project Structure

```text
crates/python-bridge/src/request.rs        request.form()
python/ferro/admin/{__init__,site,options,forms,html,views}.py
python/ferro/admin/static/admin.css
python/ferro/app.py                        App.add_admin()
tests/python/admin/test_{pages,forms,access}.py
```
