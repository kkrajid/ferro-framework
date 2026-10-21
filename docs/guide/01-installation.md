# Installation

## Requirements

- **Python 3.12 or newer** (CPython). One wheel per platform works on every version from 3.12 up.
- **Linux** (x86_64, aarch64, including Alpine/musl), **macOS** (Intel and Apple silicon) or
  **Windows** (x64).
- A database: **SQLite** needs nothing (it is built in); **PostgreSQL** needs only a server — the
  driver is part of Ferro, there is nothing else to install.

You do **not** need Rust to use Ferro; the engine ships compiled inside the wheel.

## Install

Always use a virtual environment per project:

```bash
python -m venv .venv
```

```bash
source .venv/bin/activate          # Windows: .venv\Scripts\activate
```

```bash
pip install ferro-framework
```

The package is called `ferro-framework` on PyPI (the name `ferro` belongs to an unrelated
project); you still `import ferro` and run the `ferro` command.

Until the first release is on PyPI, install from GitHub (this builds the engine, so it needs a
[Rust toolchain](https://rustup.rs) 1.85+; it takes a few minutes):

```bash
pip install "git+https://github.com/kkrajid/ferro-framework"
```

or from a wheel file someone built for you:

```bash
pip install ferro_framework-0.6.0rc2-cp312-abi3-win_amd64.whl
```

Check it:

```bash
ferro --version
```

```text
Ferro 0.6.0rc2
```

## Create a project

```bash
ferro new shop
```

```text
shop/
├── app.py            # the application: App(), routes
├── models.py         # database models
├── admin.py          # models shown in the admin
├── migrations/       # generated migration files (commit them)
├── tests/test_app.py # tests that pass out of the box
├── .env              # local settings (not committed)
├── .env.example      # template for .env (committed)
├── pyproject.toml    # depends on ferro; pytest configuration
└── README.md
```

```bash
cd shop
ferro makemigrations && ferro migrate
ferro dev
```

Open <http://127.0.0.1:8000/>. Continue with the [tutorial](02-tutorial.md).

## Editor support

Ferro ships type information (`py.typed` and stubs for the native module), so editors and type
checkers (Pyright/Pylance, mypy) know the API. Handlers are ordinary functions; models are
ordinary classes.

## Upgrading

```bash
pip install --upgrade ferro-framework
```

Read the [changelog](../../CHANGELOG.md) first. Within a minor series (0.5.x) nothing public
changes; see the [stability policy](../stability.md). After upgrading, run
`ferro makemigrations --check` — if Ferro's own models (users, sessions, cache) changed, it tells
you a migration is needed.

## Building from source (contributors)

```bash
git clone https://github.com/kkrajid/ferro-framework && cd ferro-framework
```

```bash
uv venv --python 3.13 .venv
```

```bash
uv pip install --python .venv maturin pytest httpx ruff trustme h2 pydantic
```

```bash
uv run --python .venv maturin develop --uv
```

```bash
uv run --python .venv pytest
```

```bash
cargo test --workspace
```
