"""Query expressions: ``Q`` (combinable conditions), ``F`` (field references with arithmetic),
aggregates (``Count``, ``Sum``, ``Avg``, ``Min``, ``Max``), and — for ``annotate()`` — values,
database functions, ``Case``/``When``, ``Subquery``/``Exists`` with ``OuterRef``.

```python
await Product.objects.filter(Q(price__lt=100) | Q(tags__name="sale"), ~Q(stock=0))
await Product.objects.filter(pk=pk).update(stock=F("stock") - 1)
await Product.objects.aggregate(total=Sum("price"), n=Count("id"))
await Product.objects.annotate(
    label=Case(When(stock=0, then=Value("sold out")), default=Upper("name")),
    reviewed=Exists(Review.objects.filter(product=OuterRef("pk"))),
)
```

These objects only describe; the native core validates names and builds the SQL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ferro.models.queryset import QuerySet

__all__ = [
    "Abs",
    "Aggregate",
    "Avg",
    "Case",
    "Coalesce",
    "Concat",
    "Count",
    "Exists",
    "Expression",
    "F",
    "Func",
    "Greatest",
    "Least",
    "Length",
    "Lower",
    "Max",
    "Min",
    "Now",
    "OuterRef",
    "Q",
    "Subquery",
    "Sum",
    "Trim",
    "Upper",
    "Value",
    "When",
]


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

    def __xor__(self, other: object) -> Q:
        """Exactly one of the two conditions holds."""
        if not isinstance(other, Q):
            return NotImplemented
        return (self & ~other) | (~self & other)

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

    def __mod__(self, other: object) -> Combined:
        return self._op("%", other)

    def __rmod__(self, other: object) -> Combined:
        return self._op("%", other, reverse=True)


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


class OuterRef(Expression):
    """A field of the outer query, inside the queryset given to ``Subquery()``/``Exists()``::

    Exists(Review.objects.filter(product=OuterRef("pk")))
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def _ferro_expr(self) -> tuple[Any, ...]:
        return ("OUTER", self.name)

    def __repr__(self) -> str:
        return f"OuterRef({self.name!r})"


class Value(Expression):
    """A constant in an expression (``Value("sold out")``, ``Value(0)``)."""

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value

    def _ferro_expr(self) -> tuple[Any, ...]:
        return ("L", self.value)

    def __repr__(self) -> str:
        return f"Value({self.value!r})"


def _argument(value: object) -> tuple[Any, ...]:
    """Function arguments: strings name fields (as in Django); other plain values are constants."""
    if isinstance(value, Expression):
        return value._ferro_expr()
    if isinstance(value, str):
        return ("F", value)
    return ("L", value)


class Func(Expression):
    """A database function over expressions; strings name fields (``Lower("name")``)."""

    function = ""
    __slots__ = ("arguments",)

    def __init__(self, *expressions: Any) -> None:
        self.arguments = expressions

    def _ferro_expr(self) -> tuple[Any, ...]:
        return ("FN", self.function, [_argument(argument) for argument in self.arguments])

    def __repr__(self) -> str:
        return f"{type(self).__name__}({', '.join(repr(a) for a in self.arguments)})"


class Lower(Func):
    function = "Lower"
    __slots__ = ()


class Upper(Func):
    function = "Upper"
    __slots__ = ()


class Length(Func):
    """Number of characters."""

    function = "Length"
    __slots__ = ()


class Trim(Func):
    function = "Trim"
    __slots__ = ()


class Abs(Func):
    function = "Abs"
    __slots__ = ()


class Concat(Func):
    """Joins texts; a NULL part counts as empty text."""

    function = "Concat"
    __slots__ = ()


class Coalesce(Func):
    """The first argument that is not NULL."""

    function = "Coalesce"
    __slots__ = ()


class Greatest(Func):
    """The largest argument (PostgreSQL ignores NULLs; SQLite returns NULL if any is NULL)."""

    function = "Greatest"
    __slots__ = ()


class Least(Func):
    """The smallest argument (NULL handling as in :class:`Greatest`)."""

    function = "Least"
    __slots__ = ()


class Now(Func):
    """The database's current date and time (UTC)."""

    function = "Now"
    __slots__ = ()

    def __init__(self) -> None:
        super().__init__()


class When:
    """A branch of :class:`Case`: ``When(stock=0, then=Value("sold out"))`` or
    ``When(Q(...), then=...)``."""

    __slots__ = ("condition", "then")

    def __init__(self, condition: Q | None = None, *, then: Any, **lookups: Any) -> None:
        if condition is not None and not isinstance(condition, Q):
            raise TypeError(f"When() takes a Q object and keyword lookups, got {condition!r}")
        parts = [condition] if condition is not None else []
        if lookups:
            parts.append(Q(**lookups))
        if not parts:
            raise TypeError("When() needs a condition")
        self.condition = parts[0] if len(parts) == 1 else parts[0] & parts[1]
        self.then = then


class Case(Expression):
    """The value of the first matching :class:`When`, else ``default`` (NULL by default)."""

    __slots__ = ("default", "whens")

    def __init__(self, *whens: When, default: Any = None) -> None:
        if not whens or not all(isinstance(when, When) for when in whens):
            raise TypeError("Case() takes one or more When() objects")
        self.whens = whens
        self.default = default

    def _ferro_expr(self) -> tuple[Any, ...]:
        return (
            "CASE",
            [(when.condition._tree(), _argument(when.then)) for when in self.whens],
            _argument(self.default),
        )


class Subquery(Expression):
    """The first value of a one-column queryset (``values_list("field")``, or its primary
    key), usually filtered with :class:`OuterRef`."""

    __slots__ = ("queryset",)

    def __init__(self, queryset: QuerySet[Any]) -> None:
        self.queryset = queryset

    def _ferro_expr(self) -> tuple[Any, ...]:
        return ("SUB", self.queryset._query)


class Exists(Expression):
    """Whether the queryset finds any row (``True``/``False``)."""

    __slots__ = ("queryset",)

    def __init__(self, queryset: QuerySet[Any]) -> None:
        self.queryset = queryset

    def _ferro_expr(self) -> tuple[Any, ...]:
        return ("EXISTS", self.queryset._query)


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
