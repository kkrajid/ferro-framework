# Feature Specification: Packaging and Release

**Feature Branch**: `016-packaging-release`

**Created**: 2026-10-11

**Status**: Implemented (publishing itself is a maintainer action)

**Input**: User description: "Release/packaging illa. PyPI wheels illa, CI full run illa" —
make Ferro installable with `pip install ferro` and releasable by a repeatable process.

## Clarifications

### Session 2026-10-11

- Q: Which platforms get wheels? → A: Linux x86_64 and aarch64 (manylinux 2.28), Linux x86_64
  musl (Alpine), macOS x86_64 and arm64, Windows x64. One abi3 wheel per platform covers
  CPython 3.12+.
- Q: How is PyPI publishing authorized? → A: PyPI trusted publishing from the release workflow
  on a `v*` tag, in a protected `pypi` environment; no API token is stored anywhere. Creating
  the PyPI project/trusted publisher and pushing the tag are maintainer actions.
- Q: Version for the first release? → A: `0.5.0rc1` (Cargo `0.5.0-rc.1`), one version for the
  Rust workspace and the Python package (maturin reads it from Cargo).
- Q: How is a wheel proven to work? → A: Each CI wheel is installed into a clean virtual
  environment without the source tree and a smoke test runs `ferro new`, migrations, a real
  server (probes) and the ORM.
- Q: License files? → A: Dual MIT OR Apache-2.0 (as declared in Cargo and pyproject), both texts
  at the repository root and inside the wheels.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Install from a wheel (Priority: P1)

A developer runs `pip install ferro` on Linux, macOS or Windows without a Rust compiler and gets a
working framework.

**Independent Test**: Build a wheel, install it into a fresh virtual environment, run
`scripts/smoke_test.py`.

**Acceptance Scenarios**:

1. **Given** a built wheel, **When** installed in a clean environment, **Then** `ferro --version`
   shows the release version and the smoke test passes.
2. **Given** the source distribution and a Rust toolchain, **Then** `pip install` builds it.

### User Story 2 - Repeatable release (Priority: P1)

A maintainer tags `v0.5.0rc1`; CI builds all wheels and the sdist, installs and tests them on
three operating systems and two Python versions, publishes to PyPI and creates a GitHub release.

**Acceptance Scenarios**:

1. **Given** a pull request, **Then** wheels and sdist are built and install-tested (no publish).
2. **Given** a `v*` tag, **Then** the same artifacts are published only if every test passed.
3. **Given** CI, **Then** the full test suite (Rust, Python on 3 OS × 2 versions, PostgreSQL)
   runs on every pull request.

## Requirements *(mandatory)*

- **FR-001**: Version `0.5.0rc1` everywhere (`Cargo.toml` workspace, `ferro.__version__`,
  wheel metadata).
- **FR-002**: Package metadata: description, readme, license (MIT OR Apache-2.0), license files,
  classifiers (status, OS, Python versions, framework), project URLs, keywords.
- **FR-003**: Release workflow per the clarifications; `--locked` builds.
- **FR-004**: `scripts/smoke_test.py` runs against an installed package only.
- **FR-005**: `CHANGELOG.md` with the release's entry; release steps documented in
  `docs/releasing.md`.
- **FR-006**: The wheel contains the Python package, the native module, `py.typed`, the type stub
  and the license files, and no tests, benchmarks or debug files (`.pdb`).

## Success Criteria *(mandatory)*

- **SC-001**: A locally built release wheel installs into a clean venv and passes the smoke test
  (Windows, measured here); CI does the same on Linux and macOS.
- **SC-002**: The wheel's file list matches FR-006.

## Assumptions

- PyPI account, trusted publisher setup, GitHub repository and tag push are done by the
  maintainer (they need the maintainer's credentials).
