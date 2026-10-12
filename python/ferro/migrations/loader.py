"""Loads migration files (``NNNN_name.py``) and checks that history is linear."""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ferro.migrations.errors import MigrationError
from ferro.migrations.ops import Migration

__all__ = ["LoadedMigration", "load_migrations"]

_FILE = re.compile(r"^(\d{4})_([A-Za-z0-9_]+)\.py$")


@dataclass
class LoadedMigration:
    name: str
    number: int
    path: Path
    migration: Migration

    def as_native(self) -> tuple[str, list[dict[str, Any]]]:
        return self.name, [operation.to_dict() for operation in self.migration.operations]


def _import(path: Path) -> Migration:
    spec = importlib.util.spec_from_file_location(f"ferro_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise MigrationError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise MigrationError(f"{path.name} failed to load: {type(exc).__name__}: {exc}") from exc
    migration = getattr(module, "migration", None)
    if not isinstance(migration, Migration):
        raise MigrationError(f"{path.name} must define `migration = Migration(...)`")
    return migration


def load_migrations(directory: Path | str) -> list[LoadedMigration]:
    """Migrations in order; raises ``MigrationError`` for gaps, conflicts or broken links."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    found: dict[int, Path] = {}
    for path in sorted(directory.glob("*.py")):
        if path.name == "__init__.py":
            continue
        match = _FILE.match(path.name)
        if match is None:
            raise MigrationError(
                f"unexpected file {path.name} in {directory} (migration files are NNNN_name.py)"
            )
        number = int(match.group(1))
        if number in found:
            raise MigrationError(
                f"conflicting migrations {found[number].name} and {path.name}: history must be "
                f"linear; renumber one of them and fix its dependencies"
            )
        found[number] = path
    loaded: list[LoadedMigration] = []
    for expected, number in enumerate(sorted(found), start=1):
        path = found[number]
        if number != expected:
            raise MigrationError(f"migration {expected:04d} is missing (found {path.name})")
        migration = _import(path)
        wanted = [loaded[-1].name] if loaded else []
        if list(migration.dependencies) != wanted:
            raise MigrationError(
                f"{path.name} depends on {list(migration.dependencies)}, expected {wanted} "
                f"(history must be linear)"
            )
        loaded.append(LoadedMigration(path.stem, number, path, migration))
    return loaded
