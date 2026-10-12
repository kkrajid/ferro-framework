---
description: "Task list for Feature 016 — Packaging and Release"
---

# Tasks: Packaging and Release

**Input**: `specs/016-packaging-release/spec.md`

## Phase 1: Package metadata

- [X] T001 Version `0.5.0-rc.1` in `Cargo.toml` (Python `0.5.0rc1`), `Cargo.lock` updated
- [X] T002 `pyproject.toml`: SPDX license, license files, classifiers, keywords, URLs; wheel excludes `.pdb` and `__pycache__`
- [X] T003 [P] `LICENSE-MIT`, `LICENSE-APACHE`, `CHANGELOG.md`

## Phase 2: Install test (US1)

- [X] T004 `scripts/smoke_test.py`: installed package only — `ferro new`, `makemigrations`, `migrate`, `routes`, a real `ferro run` (probes, graceful stop), the ORM
- [X] T005 Local proof: `maturin build --release --locked`, wheel file list checked (FR-006), installed into a fresh venv outside the repository, smoke test passed (SC-001, Windows)

## Phase 3: Release workflow (US2)

- [X] T006 `.github/workflows/release.yml`: 6 wheels (manylinux x86_64/aarch64, musllinux, macOS x86_64/arm64, Windows x64) + sdist on PRs/main/tags; install test on 3 OS × Python 3.12/3.13; publish on `v*` tags via trusted publishing in the `pypi` environment; GitHub release
- [X] T007 `.github/workflows/ci.yml` installs the new test dependencies (`trustme`, `h2`)
- [X] T008 `docs/releasing.md`: one-time maintainer setup (GitHub, PyPI trusted publisher) and the release steps

## Implementation Notes

- **Local proof (Windows, SC-001/SC-002)**: `maturin build --release --locked` produced
  `ferro-0.5.0rc1-cp312-abi3-win_amd64.whl` (4.5 MB, 54 files: the package, `_ferro.pyd`, type
  stub, `py.typed`, both license files, an SBOM; no `.pdb`, `__pycache__` or tests) and
  `ferro-0.5.0rc1.tar.gz`. Installed with pip into a new virtual environment in a temporary
  directory outside the repository, `scripts/smoke_test.py` passed (`ferro new`,
  `makemigrations`, `migrate`, `routes`, a real `ferro run` with probes and a graceful stop, the
  ORM).
- **Version spelling**: the first smoke run showed `ferro.__version__ == "0.5.0-rc.1"` (Cargo's
  spelling from the native module) while the wheel was `0.5.0rc1`. `ferro/__init__.py` now
  converts pre-release suffixes to PEP 440, so `ferro --version`, banners and migration headers
  print `0.5.0rc1`; the wheel was rebuilt and the smoke test rerun.
- **Not done here**: Linux and macOS wheels were not built on this machine (Docker Desktop is
  stopped and memory is short); the release workflow builds and install-tests them in CI.
  Publishing (GitHub repository, PyPI trusted publisher, tag) needs the maintainer's accounts —
  steps in `docs/releasing.md`.
- **License metadata**: PEP 639 (`License-Expression: MIT OR Apache-2.0`, `License-File` ×2) is
  accepted by maturin 1.15.
