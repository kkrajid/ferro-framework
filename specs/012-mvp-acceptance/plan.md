# Implementation Plan: MVP Acceptance — E-commerce Application

**Branch**: `012-mvp-acceptance` | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

## Summary

An example shop created with `ferro new ecommerce` (models, committed migrations, JSON API,
admin, own tests) and a repository acceptance test that runs the MVP Definition of Done command
sequence and the shopping journey against a real server on SQLite and PostgreSQL
([research.md](research.md)). No framework changes are planned; any gap found becomes a
framework fix with a regression test.

## Technical Context

**Language/Version**: Python ≥ 3.12 (the example); the framework as built in Features 001–011 ·
**Dependencies**: none (Ferro only) · **Storage**: SQLite and PostgreSQL ·
**Testing**: the shop's pytest suite (TestClient), `tests/integration/test_ecommerce.py`
(subprocess commands + HTTP).

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| I–IV | PASS | The example uses only Ferro APIs; queries go through the ORM (bound parameters). |
| V | PASS | Passwords validated and hashed by Ferro; only card last-four stored; sessions and CSRF defaults unchanged. |
| VI | PASS | Whole operations per ORM call; no per-row Python loops on hot paths beyond order lines. |
| VII | PASS | Acceptance test written first and observed failing (no migrations); both backends. |
| VIII | PASS | No performance claims. |
| IX | PASS | The example is the Django-familiar workflow from §21. |
| X | PASS | Roadmap 012 / vision §21. |

## Project Structure

```text
examples/ecommerce/          app.py, models.py, admin.py, migrations/0001_initial.py,
                             tests/test_shop.py, README.md, pyproject.toml, .env.example
tests/integration/test_ecommerce.py
```
