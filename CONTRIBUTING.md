# Contributing to Ferro

Thank you for helping. Ferro is a young project, so every bug report, test, benchmark run and
documentation fix makes a real difference. You do not need to know Rust to contribute: most of
the framework users touch is Python, and the docs and tests are a good place to start.

By taking part you agree to follow the [code of conduct](CODE_OF_CONDUCT.md).

## Ways to help

- **Report a bug.** Open an issue with the bug template: the Ferro version (`ferro --version`),
  your OS and Python version, the smallest code that shows the problem, what you expected and
  what happened. Security problems go through [SECURITY.md](SECURITY.md), never a public issue.
- **Suggest a feature.** Open an issue with the feature template and describe the problem first,
  then the API you have in mind. Features are discussed before they are built, so nobody spends
  a week on a pull request that cannot be merged.
- **Improve the docs.** The guide is in [docs/guide/](docs/guide/). Unclear sentences, missing
  examples and typos are all welcome fixes.
- **Run the benchmarks on your machine** (see [Benchmarks](#benchmarks)) and share the results
  in an issue or discussion; numbers from other hardware are very useful.
- **Pick an issue** labelled `good first issue` or `help wanted`, and comment that you are
  working on it.

## Setting up

You need Rust (stable, via [rustup](https://rustup.rs)), [uv](https://docs.astral.sh/uv/) and
CPython 3.12 or newer. PostgreSQL is optional: without it the PostgreSQL tests are skipped.

```bash
git clone https://github.com/kkrajid/ferro-framework
cd ferro-framework
uv venv --python 3.13 .venv
uv pip install --python .venv --group dev
uv run --python .venv maturin develop --uv     # builds the native module (debug)
```

After changing Rust code, run `maturin develop --uv` again; Python changes take effect
immediately. `maturin develop` makes a debug build: fine for tests, 3–5 times slower than a
release build. Use `maturin develop --uv --release` before measuring anything.

## Running the checks

CI runs the same commands on Linux, macOS and Windows with Python 3.12 and 3.13. Before you
open a pull request, run:

```bash
uv run --python .venv ruff check python tests examples
uv run --python .venv ruff format --check python tests examples
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
uv run --python .venv pytest
```

- `pytest` runs `tests/python` (unit tests), `tests/integration` (real servers and processes)
  and `tests/stress` (load and failure tests). While working, run the file you are changing:
  `pytest tests/python/test_requests_responses.py -x`.
- **PostgreSQL**: ORM tests run on SQLite and PostgreSQL. With PostgreSQL's binaries
  (`initdb`, `pg_ctl`, `psql`) on `PATH`, the tests start a temporary cluster on their own; or
  point them at a server you have with `FERRO_TEST_POSTGRES_URL=postgresql://user:pass@host/db`.
- **Rust dependencies**: if you add or update a crate, regenerate the licence notices with
  the command in [about.toml](about.toml) (`cargo install cargo-about` first); CI fails while
  `THIRD-PARTY-LICENSES.txt` is out of date.

## Making a change

1. **Talk first for anything bigger than a fix.** Open or comment on an issue so we can agree
   on the behaviour and the API.
2. **Branch from `main`**, one topic per pull request.
3. **Write the test first** when you fix a bug: a test that fails without your change and
   passes with it. New features need tests on both databases when they touch the ORM.
4. **Keep the public API stable.** Names in each module's `__all__` are listed in
   [docs/stability.md](docs/stability.md) and checked by `tests/python/test_public_api.py`.
   Adding names is fine; renaming or removing them needs a discussion first.
5. **Update the docs** in [docs/guide/](docs/guide/) for anything users see, and add a line to
   the `[Unreleased]` section of [CHANGELOG.md](CHANGELOG.md) (Added, Changed, Fixed or
   Security).
6. **Open the pull request** with the template filled in. CI must be green; a maintainer will
   review it, usually within a few days.

### Larger features

Ferro is built feature by feature with [Spec Kit](https://github.com/github/spec-kit): each
feature has a folder in [specs/](specs/) with a specification (what and why), and usually a
plan, research notes and tasks. For a large feature, a short `spec.md` in a new
`specs/NNN-name/` folder is the best way to start the discussion. The non-negotiable
principles are in [.specify/memory/constitution.md](.specify/memory/constitution.md).

## Code style

- **Python**: formatted and linted by `ruff` (settings in `pyproject.toml`); type hints on
  public functions; docstrings explain what a function does for the user, with an example
  when it helps.
- **Rust**: `rustfmt` and `clippy -D warnings`. Only `crates/shared` may contain `unsafe`
  code; every other crate has `#![forbid(unsafe_code)]`.
- **Errors**: messages say what went wrong and what to do about it
  (`no database URL; pass --database-url, set FERRO_DATABASE_URL or use App(database_url=...)`).
- **Commits**: a short summary line (`Fix: ...`, `Tests: ...`, `Feature 027: ...`), then a
  body that explains why when it is not obvious.

## Where things live

```text
python/ferro          the Python API (routing, ORM models, auth, admin, jobs, CLI, ...)
crates/http           the HTTP engine (actix-web), limits, request bodies
crates/router         path patterns and the routing trie
crates/core           settings, requests, responses, security policy, multipart
crates/orm            query AST and SQL compiler
crates/sqlite         SQLite backend          crates/postgres   PostgreSQL backend
crates/migrations     schema diff and migration SQL
crates/python-bridge  the native module ferro._ferro (asyncio bridge, JSON, ORM bindings)
tests/                python, integration, stress, benchmarks
docs/                 the user guide, deployment, stability and security documents
examples/             example applications (the e-commerce shop is the acceptance test)
```

## Benchmarks

```bash
uv pip install --python .venv fastapi uvicorn httptools flask waitress psutil
uv run --python .venv maturin develop --uv --release
python tests/benchmarks/run_025_compare.py     # throughput and latency (needs oha on PATH)
python tests/benchmarks/run_026_memory.py      # memory
```

The [Benchmark workflow](.github/workflows/benchmark.yml) runs the comparison on a fresh
GitHub Linux runner with pinned versions; anyone with a fork can run it from the Actions tab.

## Releases

Maintainers release from tags; the steps are in [docs/releasing.md](docs/releasing.md).

## Licence

Ferro is dual-licensed under [MIT](LICENSE-MIT) and [Apache 2.0](LICENSE-APACHE). Unless you
say otherwise, any contribution you submit is licensed the same way, without additional terms.
