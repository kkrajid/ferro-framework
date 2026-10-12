"""Field declarations. Fields are plain class attributes describing columns; values live on
instances. Validation and conversion happen in the native core when values are written."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

__all__ = [
    "JSON",
    "UUID",
    "BigInteger",
    "Boolean",
    "Date",
    "DateTime",
    "Decimal",
    "Field",
    "Float",
    "Integer",
    "String",
    "Text",
    "Time",
]


class _NotProvided:
    def __repr__(self) -> str:
        return "NOT_PROVIDED"


NOT_PROVIDED: Any = _NotProvided()


class Field:
    """Base class of every field type.

    Options: ``null`` (allow None), ``default`` (value or zero-argument callable), ``unique``,
    ``index``, ``primary_key`` and ``column`` (database column name; defaults to the attribute).
    """

    kind: ClassVar[str] = ""

    def __init__(
        self,
        *,
        null: bool = False,
        default: Any = NOT_PROVIDED,
        unique: bool = False,
        index: bool = False,
        primary_key: bool = False,
        column: str | None = None,
    ) -> None:
        self.name: str = ""
        self.null = null
        self.default = default
        self.unique = unique
        self.index = index
        self.primary_key = primary_key
        self.column = column

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def has_default(self) -> bool:
        return self.default is not NOT_PROVIDED

    def get_default(self) -> Any:
        default: Any | Callable[[], Any] = self.default
        return default() if callable(default) else default

    def spec(self) -> dict[str, Any]:
        """Definition handed to the native model registry."""
        return {
            "name": self.name,
            "kind": self.kind,
            "column": self.column,
            "null": self.null,
            "unique": self.unique,
            "index": self.index,
            "primary_key": self.primary_key,
        }

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name or '(unbound)'}>"


class String(Field):
    """Text with a maximum length in characters (``varchar``)."""

    kind = "String"

    def __init__(self, max_length: int, **options: Any) -> None:
        super().__init__(**options)
        self.max_length = max_length

    def spec(self) -> dict[str, Any]:
        return {**super().spec(), "max_length": self.max_length}


class Integer(Field):
    """32-bit integer."""

    kind = "Integer"


class BigInteger(Field):
    """64-bit integer."""

    kind = "BigInteger"


class Boolean(Field):
    kind = "Boolean"


class Decimal(Field):
    """Exact decimal number with ``max_digits`` total digits, ``decimal_places`` after the point.

    On SQLite values are compared numerically and read back quantized to ``decimal_places``;
    up to 15 significant digits are stored exactly.
    """

    kind = "Decimal"

    def __init__(self, max_digits: int, decimal_places: int, **options: Any) -> None:
        super().__init__(**options)
        self.max_digits = max_digits
        self.decimal_places = decimal_places

    def spec(self) -> dict[str, Any]:
        return {
            **super().spec(),
            "max_digits": self.max_digits,
            "decimal_places": self.decimal_places,
        }


class Text(Field):
    """Unbounded text."""

    kind = "Text"


class Float(Field):
    """Double-precision float. NaN and infinity are rejected."""

    kind = "Float"


class Date(Field):
    """``datetime.date`` (a ``datetime`` is rejected)."""

    kind = "Date"


class DateTime(Field):
    """Timezone-aware ``datetime``; stored in UTC and returned as UTC. Naive values are
    rejected."""

    kind = "DateTime"


class Time(Field):
    """Naive ``datetime.time``."""

    kind = "Time"


class UUID(Field):
    """``uuid.UUID`` (strings are accepted and parsed)."""

    kind = "UUID"


class JSON(Field):
    """Any JSON-compatible value, encoded with Ferro's JSON encoder."""

    kind = "JSON"
