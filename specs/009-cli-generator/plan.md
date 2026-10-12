# Implementation Plan: CLI & Project Generator

**Branch**: `009-cli-generator` | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

## Summary

New CLI commands `ferro new`, `ferro shell`, `ferro db shell`, `.env` loading for every command,
and `ferro.testing.TestClient` used by generated projects ([research.md](research.md)). All in
Python: these are developer-tooling glue around existing native functionality.

## Technical Context

**Language/Version**: Python ≥ 3.12 · **Dependencies**: none new
**Testing**: CLI subprocess tests (`tests/integration/test_project.py`), unit tests for `.env`
parsing and `TestClient`; the generated project's own test suite runs inside the test.

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| I | PASS | Tooling only; no request-path code in Python added. |
| II–IV | PASS | No engine/ORM/SQL changes. |
| V | PASS | `.env` never overrides real environment; DB password not on command lines; `.env` ignored by the generated `.gitignore`. |
| VI | PASS | No runtime path changes. |
| VII | PASS | Tests first. |
| VIII | PASS | No performance claims. |
| IX | PASS | `startproject`/`shell`/`dbshell` equivalents. |
| X | PASS | Roadmap 009 (createsuperuser already in 007). |

## Project Structure

```text
python/ferro/project.py      templates + generator
python/ferro/dotenv.py       .env parser
python/ferro/shell.py        asyncio REPL
python/ferro/testing.py      TestClient
python/ferro/cli.py          new / shell / db shell / .env loading
tests/integration/test_project.py, tests/python/test_dotenv.py, tests/python/test_testing.py
```
