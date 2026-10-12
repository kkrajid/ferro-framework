"""``ferro new``: generates a small, runnable project (Feature 009)."""

from __future__ import annotations

import importlib.util
import keyword
import sys
from pathlib import Path
from string import Template

from ferro.exceptions import ConfigurationError

__all__ = ["create_project", "validate_name"]

APP = '''"""$name: a Ferro application.

    ferro makemigrations && ferro migrate
    ferro dev
"""

from decimal import Decimal

import admin  # noqa: F401 - registers the models with the admin site
from ferro import App, Response
from models import Product

app = App()  # settings come from .env, FERRO_* variables and the command line
app.add_admin()


@app.get("/")
async def home():
    return {"name": "$name", "status": "ok"}


@app.get("/products")
async def list_products():
    return [product.to_dict() for product in await Product.objects.order_by("name")]


@app.post("/products")
async def create_product(request):
    data = await request.json()
    product = await Product.objects.create(name=data["name"], price=Decimal(str(data["price"])))
    return Response(product.to_dict(), status=201)


@app.get("/products/{id}")
async def get_product(id: int):
    product = await Product.objects.get_or_404(pk=id)
    return product.to_dict()
'''

MODELS = '''"""Database models.

After changing them run `ferro makemigrations` and `ferro migrate`.
"""

from datetime import UTC, datetime

from ferro import models


def now() -> datetime:
    return datetime.now(UTC)


class Product(models.Model):
    name = models.String(max_length=200)
    price = models.Decimal(max_digits=10, decimal_places=2)
    created = models.DateTime(default=now)

    def __str__(self) -> str:
        return self.name
'''

ADMIN = '''"""Models shown in the admin at /admin.

Log in with a user created by `ferro createsuperuser`.
"""

from ferro.admin import ModelAdmin, admin
from models import Product

admin.register(
    Product,
    ModelAdmin(list_display=["name", "price", "created"], search_fields=["name"]),
)
'''

TEST = """from ferro.testing import TestClient

from app import app


def test_home():
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.json() == {"name": "$name", "status": "ok"}


def test_products():
    with TestClient(app, database_url="sqlite:///:memory:") as client:
        created = client.post("/products", json={"name": "Phone", "price": "499.00"})
        assert created.status_code == 201
        product_id = created.json()["id"]
        assert client.get(f"/products/{product_id}").json()["name"] == "Phone"
        assert [p["name"] for p in client.get("/products").json()] == ["Phone"]
        assert client.get("/products/999").status_code == 404
"""

ENV = """# Local settings for `ferro` commands (not committed: see .gitignore).
FERRO_DATABASE_URL=sqlite:///db.sqlite3
"""

ENV_EXAMPLE = """# Copy to .env and adjust. Every setting can also come from the environment.
FERRO_DATABASE_URL=sqlite:///db.sqlite3
# FERRO_DATABASE_URL=postgresql://user:password@localhost:5432/$name
# FERRO_RATE_LIMIT=100/minute
"""

GITIGNORE = """__pycache__/
*.py[cod]
.venv/
.env
*.db
*.sqlite3
.pytest_cache/
"""

PYPROJECT = """[project]
name = "$name"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["ferro"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
"""

README = """# $name

A [Ferro](https://github.com/kkrajid/ferro-framework) application.

```bash
ferro makemigrations && ferro migrate      # create the database (settings in .env)
FERRO_SUPERUSER_PASSWORD=... ferro createsuperuser --username admin --no-input
ferro dev                                   # http://127.0.0.1:8000/ and /admin/
ferro shell                                 # Python with await, models and the database
python -m pytest                            # run the tests
```
"""

FILES = {
    "app.py": APP,
    "models.py": MODELS,
    "admin.py": ADMIN,
    "tests/test_app.py": TEST,
    "migrations/.gitkeep": "",
    ".env": ENV,
    ".env.example": ENV_EXAMPLE,
    ".gitignore": GITIGNORE,
    "pyproject.toml": PYPROJECT,
    "README.md": README,
}


def _importable(name: str) -> bool:
    """Whether ``name`` is an installed or standard module (the working directory, where the
    project itself may already exist, does not count)."""
    here = Path.cwd().resolve()
    saved = sys.path[:]
    sys.path[:] = [p for p in saved if p not in ("", ".") and Path(p).resolve() != here]
    try:
        return name in sys.stdlib_module_names or importlib.util.find_spec(name) is not None
    finally:
        sys.path[:] = saved


def validate_name(name: str) -> None:
    if not name.isidentifier() or not name.isascii():
        raise ConfigurationError(
            f"'{name}' is not a valid project name: use letters, digits and underscores, "
            "starting with a letter"
        )
    if keyword.iskeyword(name) or keyword.issoftkeyword(name):
        raise ConfigurationError(f"'{name}' is a Python keyword; choose another project name")
    if _importable(name):
        raise ConfigurationError(
            f"'{name}' conflicts with an existing Python module; choose another project name"
        )


def create_project(target: str, *, force: bool = False, base: Path | None = None) -> Path:
    """Writes the project files; returns the project directory."""
    base = base or Path.cwd()
    directory = base if target == "." else base / target
    name = directory.resolve().name if target == "." else target
    validate_name(name)
    if directory.exists() and any(directory.iterdir()) and not force:
        raise ConfigurationError(
            f"directory '{directory}' is not empty; use --force to write into it anyway"
        )
    for relative, template in FILES.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not force:
            continue
        path.write_text(Template(template).safe_substitute(name=name), encoding="utf-8")
        if path.name == ".env":
            # It will hold database credentials: readable by its owner only (POSIX).
            path.chmod(0o600)
    return directory
