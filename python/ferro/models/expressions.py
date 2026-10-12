"""Query expressions: ``Q`` (combinable conditions), ``F`` (field references with arithmetic) and
aggregates (``Count``, ``Sum``, ``Avg``, ``Min``, ``Max``).

```python
await Product.objects.filter(Q(price__lt=100) | Q(tags__name="sale"), ~Q(stock=0))
await Product.objects.filter(pk=pk).update(stock=F("stock") - 1)
await Product.objects.aggregate(total=Sum("price"), n=Count("id"))
```

These objects only describe; the native core validates names and builds the SQL.
"""

from __future__ import annotations

from typing import Any

__all__ = ["Aggregate", "Avg", "Count", "Expression", "F", "Max", "Min", "Q", "Sum"]


class Q:
    """Conditions combined with ``&`` (AND), ``|`` (OR) and ``~`` (NOT)."""

    AND = "AND"
    OR = "OR"

    __slots__ = ("children", "connector", "negated")

    def __init__(self, *conditions: Q, **lookups: Any) -> None:
        for condition in conditions:
            if not isinstance(condition, Q):
                raise TypeError(f"Q() takes Q objects and keyword lookups, got {condition!r}")
        self.children: list[Q | tuple[str, Any]] = [*conditions, *lookups.items()]
        self.connector = self.AND
        self.negated = False

    def _combine(self, other: object, connector: str) -> Q:
        if not isinstance(other, Q):
            return NotImplemented
        combined = Q()
        combined.connector = connector
        combined.children = [self, other]
        return combined

    def __and__(self, other: object) -> Q:
        return self._combine(other, self.AND)

    def __or__(self, other: object) -> Q:
        return self._combine(other, self.OR)

    def __invert__(self) -> Q:
        inverted = Q()
        inverted.children = list(self.children)
        inverted.connector = self.connector
        inverted.negated = not self.negated
        return inverted

    def _tree(self) -> tuple[str, bool, list[Any]]:
        """``(connector, negated, children)`` for the native core."""
        children = [c._tree() if isinstance(c, Q) else c for c in self.children]
        return (self.connector, self.negated, children)

    def __repr__(self) -> str:
        inner = f" {self.connector} ".join(repr(c) for c in self.children)
        return f"<Q: {'NOT ' if self.negated else ''}({inner})>"


class Expression:
    """Base of ``F`` and arithmetic combinations; supports ``+ - * /`` with numbers and
    other expressions."""

    __slots__ = ()

    def _ferro_expr(self) -> tuple[Any, ...]:
        raise NotImplementedError

    def _op(self, symbol: str, other: object, reverse: bool = False) -> Combined:
        return Combined(other, symbol, self) if reverse else Combined(self, symbol, other)

    def __add__(self, other: object) -> Combined:
        return self._op("+", other)

    def __radd__(self, other: object) -> Combined:
        return self._op("+", other, reverse=True)

    def __sub__(self, other: object) -> Combined:
        return self._op("-", other)

    def __rsub__(self, other: object) -> Combined:
        return self._op("-", other, reverse=True)

    def __mul__(self, other: object) -> Combined:
        return self._op("*", other)

    def __rmul__(self, other: object) -> Combined:
        return self._op("*", other, reverse=True)

    def __truediv__(self, other: object) -> Combined:
        return self._op("/", other)

    def __rtruediv__(self, other: object) -> Combined:
        return self._op("/", other, reverse=True)


def _expr(value: object) -> tuple[Any, ...]:
    return value._ferro_expr() if isinstance(value, Expression) else ("V", value)


class F(Expression):
    """A reference to a field of the queried model, evaluated by the database."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def _ferro_expr(self) -> tuple[Any, ...]:
        return ("F", self.name)

    def __repr__(self) -> str:
        return f"F({self.name!r})"


class Combined(Expression):
    __slots__ = ("left", "right", "symbol")

    def __init__(self, left: object, symbol: str, right: object) -> None:
        self.left = left
        self.symbol = symbol
        self.right = right

    def _ferro_expr(self) -> tuple[Any, ...]:
        return ("OP", self.symbol, _expr(self.left), _expr(self.right))

    def __repr__(self) -> str:
        return f"({self.left!r} {self.symbol} {self.right!r})"


class Aggregate:
    """An aggregate over a field path (``"price"``, ``"items__quantity"``).

    ``distinct=True`` aggregates distinct values; ``default`` replaces the ``None`` an aggregate
    over no rows returns.
    """

    function = ""

    def __init__(self, field: str, *, distinct: bool = False, default: Any = None) -> None:
        self.field = field
        self.distinct = distinct
        self.default = default

    def default_alias(self) -> str:
        return f"{self.field}__{self.function.lower()}"

    def _spec(self, alias: str) -> tuple[str, str, str | None, bool, Any]:
        return (alias, self.function, self.field, self.distinct, self.default)

    def __repr__(self) -> str:
        return f"{self.function}({self.field!r})"


class Count(Aggregate):
    """Number of rows (``Count()`` / ``Count("*")``) or of non-NULL values of a field path."""

    function = "Count"

    def __init__(self, field: str = "*", *, distinct: bool = False, default: Any = None) -> None:
        super().__init__(field, distinct=distinct, default=default)

    def default_alias(self) -> str:
        return "count" if self.field == "*" else super().default_alias()


class Sum(Aggregate):
    function = "Sum"


class Avg(Aggregate):
    """Average as ``float``."""

    function = "Avg"


class Min(Aggregate):
    function = "Min"


class Max(Aggregate):
    function = "Max"
