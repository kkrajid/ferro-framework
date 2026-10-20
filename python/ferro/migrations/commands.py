"""makemigrations / migrate / rollback / status. The CLI prints; these functions return data."""

from __future__ import annotations

import datetime as dt
import inspect
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ferro import __version__, _ferro
from ferro.migrations import ops
from ferro.migrations.errors import MigrationError
from ferro.migrations.loader import LoadedMigration, load_migrations
from ferro.migrations.writer import render_migration

__all__ = ["MakeResult", "make_migrations", "migrate", "rollback", "status"]

Progress = Callable[[str], None]


@dataclass
class MakeResult:
    path: Path | None
    operations: list[ops.Operation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _auto_name(operations: list[ops.Operation]) -> str:
    name = operations[0].fragment()
    if len(operations) > 1:
        name += "_and_more"
    name = re.sub(r"[^A-Za-z0-9_]", "_", name).lower()
    return name[:52]


def _model_classes(models: Iterable[type] | None) -> list[type]:
    if models is None:
        from ferro.models.base import registered_model_classes

        return registered_model_classes()
    # Many-to-many link models belong to the models that declare them.
    classes: list[type] = []
    for model in models:
        classes.append(model)
        classes.extend(r.link() for r in getattr(model, "_many_to_many", {}).values())
    return list(dict.fromkeys(classes))


def _resolve_defaults(operations: list[ops.Operation], models: list[type]) -> None:
    by_table = {model._meta.table: model for model in models}
    for operation in operations:
        if not isinstance(operation, ops.AddColumn) or operation.default is not ops.MISSING:
            continue
        column = operation.column
        if column.null or column.auto:
            continue
        model = by_table.get(operation.table)
        fields = getattr(model, "_fields", {}) if model is not None else {}
        declared = next(
            (
                f
                for f in fields.values()
                if (f.column or getattr(f, "attname", f.name)) == column.name
            ),
            None,
        )
        if declared is not None and declared.has_default() and not callable(declared.default):
            operation.default = declared.default
            continue
        owner = f"{model.__name__}.{declared.name}" if model and declared else column.name
        raise MigrationError(
            f"{owner}: cannot add a non-null field to the existing table '{operation.table}' "
            "without a default. Give it a constant default (default=...), allow NULL "
            "(null=True), or add it as nullable first and make it non-null in a later migration."
        )


def make_migrations(
    directory: Path | str,
    *,
    models: Iterable[type] | None = None,
    name: str | None = None,
    check: bool = False,
    empty: bool = False,
) -> MakeResult:
    """Writes the next migration for model changes. Needs no database connection."""
    directory = Path(directory)
    loaded = load_migrations(directory)
    dependencies = [loaded[-1].name] if loaded else []
    number = len(loaded) + 1
    if empty:
        path = _write(directory, number, name or "manual", dependencies, [])
        return MakeResult(path)
    classes = _model_classes(models)
    qualnames = [model._meta.qualname for model in classes] if models is not None else None
    dicts, warnings = _ferro.migration_diff([m.as_native() for m in loaded], qualnames)
    operations = [ops.from_dict(d) for d in dicts]
    if not operations:
        return MakeResult(None, [], warnings)
    if check:
        return MakeResult(None, operations, warnings)
    _resolve_defaults(operations, classes)
    label = name or ("initial" if not loaded else _auto_name(operations))
    path = _write(directory, number, label, dependencies, operations)
    return MakeResult(path, operations, warnings)


def _write(
    directory: Path,
    number: int,
    label: str,
    dependencies: list[str],
    operations: list[ops.Operation],
) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_]+", label):
        raise MigrationError(f"invalid migration name {label!r} (letters, digits, underscores)")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{number:04d}_{label}.py"
    path.write_text(render_migration(dependencies, operations, __version__), encoding="utf-8")
    return path


async def _history(loaded: list[LoadedMigration]) -> list[str]:
    await _ferro.ensure_migration_table()
    applied: list[str] = await _ferro.migration_history()
    names = [m.name for m in loaded]
    if applied != names[: len(applied)]:
        unknown = [name for name in applied if name not in names]
        detail = f"missing files for {unknown}" if unknown else "the order differs"
        raise MigrationError(
            f"the database history does not match the migration files ({detail}); "
            f"applied: {applied}, files: {names}"
        )
    return applied


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


async def _apply(loaded: list[LoadedMigration], index: int, forward: bool) -> None:
    """Applies (``forward``) or reverts one migration atomically, with its history row."""
    from ferro.db import transaction

    current = loaded[index]
    previous = [m.as_native() for m in loaded[:index]]
    if not current.migration.runs_python:
        await _ferro.apply_migration(previous, current.as_native(), forward, _now())
        return
    # Data migrations: Python code and SQL share one transaction, in order (reversed when
    # reverting); the history row commits with them.
    steps = list(current.migration.operations)
    if not forward:
        steps.reverse()
    async with transaction():
        for operation in steps:
            if isinstance(operation, ops.RunPython):
                function = operation.forward if forward else operation.backward
                if function is None:
                    raise MigrationError(f"{current.name}: RunPython has no backward function")
                result = function()
                if inspect.isawaitable(result):
                    await result
            else:
                single = (current.name, [operation.to_dict()])
                await _ferro.apply_migration(previous, single, forward, _now(), False)
        await _ferro.apply_migration(previous, (current.name, []), forward, _now())


async def migrate(
    directory: Path | str,
    *,
    before: Progress | None = None,
    after: Progress | None = None,
) -> list[str]:
    """Applies unapplied migrations in order, each atomically. Returns their names."""
    loaded = load_migrations(directory)
    applied = await _history(loaded)
    done: list[str] = []
    for index in range(len(applied), len(loaded)):
        current = loaded[index]
        if before:
            before(current.name)
        await _apply(loaded, index, True)
        done.append(current.name)
        if after:
            after(current.name)
    return done


async def rollback(
    directory: Path | str,
    *,
    steps: int = 1,
    to: str | None = None,
    before: Progress | None = None,
    after: Progress | None = None,
) -> list[str]:
    """Reverts applied migrations newest first. ``to`` keeps that migration (``"zero"``: none)."""
    loaded = load_migrations(directory)
    applied = await _history(loaded)
    if to is None:
        if steps < 1:
            raise MigrationError("--steps must be at least 1")
        target = max(0, len(applied) - steps)
    elif to == "zero":
        target = 0
    elif to in applied:
        target = applied.index(to) + 1
    else:
        raise MigrationError(f"{to!r} is not an applied migration; applied: {applied}")
    plan = list(reversed(range(target, len(applied))))
    # Every reverse is checked before anything changes (FR-012).
    for index in plan:
        _ferro.migration_sql(
            [m.as_native() for m in loaded[:index]], loaded[index].as_native(), False
        )
    done: list[str] = []
    for index in plan:
        current = loaded[index]
        if before:
            before(current.name)
        await _apply(loaded, index, False)
        done.append(current.name)
        if after:
            after(current.name)
    return done


async def status(directory: Path | str) -> list[tuple[str, bool]]:
    """``(name, applied)`` for every migration file, in order."""
    loaded = load_migrations(directory)
    applied = set(await _history(loaded))
    return [(m.name, m.name in applied) for m in loaded]
