"""Migration tests reuse the ORM fixtures, so every test runs on SQLite and PostgreSQL."""

from __future__ import annotations

from typing import Any

from ferro import models
from tests.python.orm.conftest import database, postgres_server  # noqa: F401 - fixtures

MODULE = "tests.python.migrations.models"


def model(name: str, fields: dict[str, Any], table: str | None = None) -> type[models.Model]:
    """Defines (or redefines) a model with a stable identity, simulating edits to models.py."""
    namespace: dict[str, Any] = {"__module__": MODULE, "__qualname__": name, **fields}
    if table:
        namespace["Meta"] = type("Meta", (), {"table": table})
    return type(name, (models.Model,), namespace)
