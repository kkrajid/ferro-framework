"""Migration operations as written in migration files::

    from ferro.migrations import Migration, ops

    migration = Migration(
        dependencies=["0001_initial"],
        operations=[ops.AddColumn("product", ops.Column("stock", "Integer"), default=0)],
    )

Operations are declarative; the native engine turns them into SQL for the connected database.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

__all__ = [
    "AddColumn",
    "AlterColumn",
    "Column",
    "CreateIndex",
    "CreateTable",
    "DropIndex",
    "DropTable",
    "Migration",
    "Operation",
    "RemoveColumn",
    "RenameColumn",
    "RenameTable",
    "from_dict",
]


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


@dataclass(frozen=True)
class Column:
    """A column definition: name, field kind (``"String"``, ``"Decimal"``, ...) and options.

    A foreign key names its target as ``references="table.column"`` with an ``on_delete`` rule
    (``"cascade"``, ``"set_null"``, ``"protect"``, ``"do_nothing"``).
    """

    name: str
    kind: str
    null: bool = False
    unique: bool = False
    primary_key: bool = False
    auto: bool = False
    max_length: int | None = None
    max_digits: int | None = None
    decimal_places: int | None = None
    references: str | None = None
    on_delete: str | None = None

    _OPTIONS: ClassVar[tuple[str, ...]] = (
        "max_length",
        "max_digits",
        "decimal_places",
        "null",
        "unique",
        "primary_key",
        "auto",
        "references",
        "on_delete",
    )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "kind": self.kind}
        for option in self._OPTIONS:
            value = getattr(self, option)
            if value not in (None, False):
                data[option] = value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Column:
        return cls(
            **{k: v for k, v in data.items() if k == "name" or k == "kind" or k in cls._OPTIONS}
        )

    def render(self) -> str:
        parts = [_q(self.name), _q(self.kind)]
        for option in self._OPTIONS:
            value = getattr(self, option)
            if value not in (None, False):
                parts.append(f"{option}={_q(value) if isinstance(value, str) else repr(value)}")
        return f"ops.Column({', '.join(parts)})"


class Operation:
    """Base class of migration operations."""

    op: ClassVar[str] = ""

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError

    def fragment(self) -> str:
        """Part of an automatic migration name."""
        raise NotImplementedError

    def render(self, indent: int, imports: set[str]) -> str:
        raise NotImplementedError


def _with_default(data: dict[str, Any], default: Any) -> dict[str, Any]:
    if default is not MISSING and default is not None:
        data["default"] = default
    return data


def _render_default(default: Any, imports: set[str]) -> str:
    if default is MISSING or default is None:
        return ""
    from ferro.migrations.writer import render_value

    return f", default={render_value(default, imports)}"


@dataclass
class CreateTable(Operation):
    table: str
    columns: Sequence[Column]
    op: ClassVar[str] = "create_table"

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, "table": self.table, "columns": [c.to_dict() for c in self.columns]}

    def describe(self) -> str:
        return f"create table {self.table}"

    def fragment(self) -> str:
        return f"create_{self.table}"

    def render(self, indent: int, imports: set[str]) -> str:
        pad, inner, deeper = " " * indent, " " * (indent + 4), " " * (indent + 8)
        columns = "".join(f"{deeper}{column.render()},\n" for column in self.columns)
        return f"ops.CreateTable(\n{inner}{_q(self.table)},\n{inner}[\n{columns}{inner}],\n{pad})"


@dataclass
class DropTable(Operation):
    table: str
    op: ClassVar[str] = "drop_table"

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, "table": self.table}

    def describe(self) -> str:
        return f"drop table {self.table}"

    def fragment(self) -> str:
        return f"delete_{self.table}"

    def render(self, indent: int, imports: set[str]) -> str:
        return f"ops.DropTable({_q(self.table)})"


@dataclass
class RenameTable(Operation):
    old: str
    new: str
    op: ClassVar[str] = "rename_table"

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, "old": self.old, "new": self.new}

    def describe(self) -> str:
        return f"rename table {self.old} to {self.new}"

    def fragment(self) -> str:
        return f"rename_{self.old}_to_{self.new}"

    def render(self, indent: int, imports: set[str]) -> str:
        return f"ops.RenameTable({_q(self.old)}, {_q(self.new)})"


@dataclass
class AddColumn(Operation):
    """Adds a column; ``default`` back-fills existing rows of a non-null column."""

    table: str
    column: Column
    default: Any = MISSING
    op: ClassVar[str] = "add_column"

    def to_dict(self) -> dict[str, Any]:
        return _with_default(
            {"op": self.op, "table": self.table, "column": self.column.to_dict()}, self.default
        )

    def describe(self) -> str:
        return f"add column {self.column.name} to {self.table}"

    def fragment(self) -> str:
        return f"add_{self.table}_{self.column.name}"

    def render(self, indent: int, imports: set[str]) -> str:
        default = _render_default(self.default, imports)
        return f"ops.AddColumn({_q(self.table)}, {self.column.render()}{default})"


@dataclass
class RemoveColumn(Operation):
    """Removes a column; ``default`` lets a rollback restore a non-null column."""

    table: str
    column: str
    default: Any = MISSING
    op: ClassVar[str] = "remove_column"

    def to_dict(self) -> dict[str, Any]:
        return _with_default(
            {"op": self.op, "table": self.table, "column": self.column}, self.default
        )

    def describe(self) -> str:
        return f"remove column {self.column} from {self.table}"

    def fragment(self) -> str:
        return f"remove_{self.table}_{self.column}"

    def render(self, indent: int, imports: set[str]) -> str:
        default = _render_default(self.default, imports)
        return f"ops.RemoveColumn({_q(self.table)}, {_q(self.column)}{default})"


@dataclass
class AlterColumn(Operation):
    table: str
    column: Column
    op: ClassVar[str] = "alter_column"

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, "table": self.table, "column": self.column.to_dict()}

    def describe(self) -> str:
        return f"alter column {self.column.name} of {self.table}"

    def fragment(self) -> str:
        return f"alter_{self.table}_{self.column.name}"

    def render(self, indent: int, imports: set[str]) -> str:
        return f"ops.AlterColumn({_q(self.table)}, {self.column.render()})"


@dataclass
class RenameColumn(Operation):
    table: str
    old: str
    new: str
    op: ClassVar[str] = "rename_column"

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, "table": self.table, "old": self.old, "new": self.new}

    def describe(self) -> str:
        return f"rename column {self.old} to {self.new} in {self.table}"

    def fragment(self) -> str:
        return f"rename_{self.table}_{self.old}_to_{self.new}"

    def render(self, indent: int, imports: set[str]) -> str:
        return f"ops.RenameColumn({_q(self.table)}, {_q(self.old)}, {_q(self.new)})"


@dataclass
class CreateIndex(Operation):
    """An index on one column (``"name"``) or several (``["cart_id", "product_id"]``);
    ``unique=True`` makes the combination unique."""

    table: str
    column: str | Sequence[str]
    name: str | None = None
    unique: bool = False
    op: ClassVar[str] = "create_index"

    def __post_init__(self) -> None:
        if self.name is None:
            joined = "_".join(self.columns)
            self.name = f"{self.table}_{joined}_{'uniq' if self.unique else 'idx'}"

    @property
    def columns(self) -> list[str]:
        return [self.column] if isinstance(self.column, str) else list(self.column)

    def to_dict(self) -> dict[str, Any]:
        data = {"op": self.op, "table": self.table, "columns": self.columns, "name": self.name}
        if self.unique:
            data["unique"] = True
        return data

    def describe(self) -> str:
        kind = "unique index" if self.unique else "index"
        return f"create {kind} {self.name} on {self.table} ({', '.join(self.columns)})"

    def fragment(self) -> str:
        return f"index_{self.table}_{'_'.join(self.columns)}"

    def render(self, indent: int, imports: set[str]) -> str:
        columns = self.columns
        target = (
            _q(columns[0])
            if len(columns) == 1
            else "[" + ", ".join(_q(column) for column in columns) + "]"
        )
        unique = ", unique=True" if self.unique else ""
        return f"ops.CreateIndex({_q(self.table)}, {target}, {_q(self.name or '')}{unique})"


@dataclass
class DropIndex(Operation):
    table: str
    name: str
    op: ClassVar[str] = "drop_index"

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, "table": self.table, "name": self.name}

    def describe(self) -> str:
        return f"drop index {self.name} on {self.table}"

    def fragment(self) -> str:
        return f"remove_index_{self.name}"

    def render(self, indent: int, imports: set[str]) -> str:
        return f"ops.DropIndex({_q(self.table)}, {_q(self.name)})"


_OPERATIONS: dict[str, type[Operation]] = {
    cls.op: cls
    for cls in (
        CreateTable,
        DropTable,
        RenameTable,
        AddColumn,
        RemoveColumn,
        AlterColumn,
        RenameColumn,
        CreateIndex,
        DropIndex,
    )
}


def from_dict(data: dict[str, Any]) -> Operation:
    """Builds an operation from the native engine's dict form."""
    data = dict(data)
    kind = data.pop("op")
    if kind == "create_table":
        return CreateTable(data["table"], [Column.from_dict(c) for c in data["columns"]])
    if kind in ("add_column", "alter_column"):
        data["column"] = Column.from_dict(data["column"])
    if kind == "create_index":
        columns = data.pop("columns", None) or [data.pop("column")]
        data["column"] = columns[0] if len(columns) == 1 else columns
    return _OPERATIONS[kind](**data)


@dataclass
class Migration:
    """One migration file's content."""

    dependencies: Sequence[str] = field(default_factory=list)
    operations: Sequence[Operation] = field(default_factory=list)
