# Implementation Plan: Packaging and Release

**Branch**: `016-packaging-release` | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

## Summary

maturin already builds an abi3 wheel (`pyo3` `abi3-py312`), so one wheel per platform covers
CPython 3.12+. This feature adds release metadata (version 0.5.0rc1, SPDX license, license files,
classifiers, URLs), a smoke test that only uses an installed package, a release workflow
(`PyO3/maturin-action` builds for 6 targets + sdist, install tests on 3 OS × 2 Python versions,
PyPI trusted publishing on tags), and maintainer documentation.

## Technical Context

**Build**: maturin 1.15 (`--release --locked`), Rust stable; manylinux 2.28 (glibc ≥ 2.28),
musllinux 1.2; macOS 13 (x86_64) and 14 (arm64) runners; Windows x64.
**Testing**: `scripts/smoke_test.py` in clean virtual environments; locally a release wheel on
Windows.
**Constraints**: no secrets in the repository (trusted publishing); wheels contain no tests,
benchmarks or debug symbols.

## Constitution Check

| Principle | Status | Evidence |
|---|---|---|
| V Secure by default | PASS | No stored tokens; publishing gated by a protected environment and passing install tests. |
| VII Test-first | PASS | The smoke test is the release gate; it runs against every built wheel. |
| VIII Measured | PASS | Release builds are the benchmark builds (`--release`). |
| X Scope | PASS | Roadmap 016; conda packages, Docker images on a registry deferred. |

## Decisions

- **abi3** instead of per-version wheels: 6 wheels instead of 12+, and new CPython versions work
  on day one.
- **manylinux 2.28** (not 2014): Rust's current toolchains target glibc 2.17+, but rustls/ring and
  the PostgreSQL TLS stack build cleanly on 2.28 images and every supported distribution has
  glibc ≥ 2.28 (RHEL 8, Debian 10, Ubuntu 18.10+).
- **sdist** included for platforms without wheels (needs a Rust toolchain).
