"""``QuerySet``: an immutable, awaitable description of a query. The query itself (lookups,
relations, values, SQL) lives in the native core; this class only forwards, and orchestrates
``prefetch_related``."""

from __future__ import annotations

from collections.abc import Generator, Iterable
from typing import TYPE_CHECKING, Any

from ferro import _ferro
from ferro.exceptions import HTTPException
from ferro.models.exceptions import TransactionError
from ferro.models.expressions import Aggregate, Q
from ferro.models.relations import prefetch_related_objects

if TYPE_CHECKING:
    from ferro.models.base import Model

__all__ = ["QuerySet"]


def _tree(conditions: tuple[Q, ...], lookups: dict[str, Any], negated: bool) -> tuple:
    children: list[Any] = []
    for condition in conditions:
        if not isinstance(condition, Q):
            raise TypeError(
                f"filter() takes Q objects and keyword lookups, got {type(condition).__name__}"
            )
        children.append(condition._tree())
    children.extend(lookups.items())
    return ("AND", negated, children)


def _aggregate_specs(
    positional: tuple[Aggregate, ...], named: dict[str, Aggregate]
) -> list[tuple[Any, ...]]:
    specs = []
    for aggregate in positional:
        if not isinstance(aggregate, Aggregate):
            raise TypeError(f"expected an aggregate (Count, Sum, ...), got {aggregate!r}")
        specs.append(aggregate._spec(aggregate.default_alias()))
    for alias, aggregate in named.items():
        if not isinstance(aggregate, Aggregate):
            raise TypeError(f"{alias}: expected an aggregate (Count, Sum, ...), got {aggregate!r}")
        specs.append(aggregate._spec(alias))
    return specs


class QuerySet[M: Model]:
    __slots__ = ("_prefetch", "_query", "_result_cache", "model")

    def __init__(self, model: type[M], query: Any = None, prefetch: tuple[str, ...] = ()) -> None:
        self.model = model
        self._query = query if query is not None else model._meta.query()
        self._prefetch = prefetch
        self._result_cache: list[Any] | None = None

    def _clone(self, query: Any, prefetch: tuple[str, ...] | None = None) -> QuerySet[M]:
        return QuerySet(self.model, query, self._prefetch if prefetch is None else prefetch)

    def _filtered(self, conditions: tuple[Q, ...], lookups: dict[str, Any], negated: bool):
        if not conditions and not lookups:
            return self._clone(self._query)
        return self._clone(self._query.filter(_tree(conditions, lookups, negated)))

    def all(self) -> QuerySet[M]:
        return self._clone(self._query)

    def filter(self, *conditions: Q, **lookups: Any) -> QuerySet[M]:
        """Records matching every condition (AND). Lookups follow relations
        (``category__name="Phones"``); ``Q`` objects combine with ``|``, ``&``, ``~``."""
        return self._filtered(conditions, lookups, False)

    def exclude(self, *conditions: Q, **lookups: Any) -> QuerySet[M]:
        """Records not matching all of the conditions; rows with NULL in those columns are
        kept."""
        return self._filtered(conditions, lookups, True)

    def order_by(self, *names: str) -> QuerySet[M]:
        """Replaces the ordering; ``"-price"`` sorts descending, ``"pk"`` is the primary key,
        ``"category__name"`` follows a relation, annotation names are allowed."""
        return self._clone(self._query.order_by(list(names)))

    def select_related(self, *paths: str) -> QuerySet[M]:
        """Loads foreign key / one-to-one objects in the same query (LEFT JOIN)."""
        return self._clone(self._query.select_related(list(paths)))

    def prefetch_related(self, *paths: str) -> QuerySet[M]:
        """Loads any relation (reverse keys, many-to-many) with one extra query per level."""
        return self._clone(self._query, self._prefetch + paths)

    def annotate(self, *positional: Aggregate, **aggregates: Aggregate) -> QuerySet[M]:
        """Adds aggregate values: per record over relations (``Count("items")``), or per group
        after ``values()``."""
        return self._clone(self._query.annotate(_aggregate_specs(positional, aggregates)))

    def values(self, *fields: str) -> QuerySet[M]:
        """Rows as dicts of the named fields (all fields when none are named)."""
        return self._clone(self._query.values(list(fields), "dicts"))

    def values_list(self, *fields: str, flat: bool = False) -> QuerySet[M]:
        """Rows as tuples, or single values with ``flat=True``."""
        return self._clone(self._query.values(list(fields), "flat" if flat else "tuples"))

    def select_for_update(self, *, nowait: bool = False, skip_locked: bool = False) -> QuerySet[M]:
        """Locks the fetched rows until the surrounding ``transaction()`` ends, so other
        transactions locking or changing them wait. ``nowait=True`` fails at once with
        ``DatabaseUnavailableError`` instead of waiting; ``skip_locked=True`` leaves locked rows
        out. Only the model's own rows are locked (not ``select_related`` ones). On SQLite the
        query is unchanged: a transaction already holds the database write lock."""
        return self._clone(self._query.select_for_update(nowait, skip_locked))

    def limit(self, count: int) -> QuerySet[M]:
        if count < 0:
            raise ValueError("limit must not be negative")
        return self._clone(self._query.window(None, count))

    def offset(self, count: int) -> QuerySet[M]:
        if count < 0:
            raise ValueError("offset must not be negative")
        return self._clone(self._query.window(count, None))

    def __getitem__(self, key: slice) -> QuerySet[M]:
        if not isinstance(key, slice):
            raise TypeError("QuerySet supports slices only; use qs[i:i + 1] or `await qs.first()`")
        if key.step not in (None, 1):
            raise ValueError("Slicing with a step is not supported")
        start, stop = key.start, key.stop
        if (start is not None and start < 0) or (stop is not None and stop < 0):
            raise ValueError("Negative indexing is not supported")
        start = start or 0
        limit = None if stop is None else max(stop - start, 0)
        return self._clone(self._query.window(start or None, limit))

    async def _run(self, query: Any) -> list[Any]:
        if query.locking:
            session = _ferro.transaction_context.get()
            if session is None or session.closed:
                raise TransactionError(
                    f"{self.model.__name__}: select_for_update() must run inside a "
                    "transaction; wrap it in `async with transaction():` (the locks last until "
                    "the transaction ends)"
                )
        found = await query.fetch()
        if self._prefetch and not query.returns_values:
            await prefetch_related_objects(found, *self._prefetch)
        return found

    async def _all(self) -> list[Any]:
        if self._result_cache is not None:
            return list(self._result_cache)
        return await self._run(self._query)

    def __await__(self) -> Generator[Any, None, list[M]]:
        return self._all().__await__()

    async def count(self) -> int:
        if self._result_cache is not None:
            return len(self._result_cache)
        return await self._query.count()

    async def exists(self) -> bool:
        if self._result_cache is not None:
            return bool(self._result_cache)
        return await self._query.exists()

    async def first(self) -> M | None:
        """First record by the queryset's ordering (primary key when unordered), or None."""
        if self._query.ordered or self._query.grouped:
            query = self._query
        else:
            query = self._query.order_by(["pk"])
        found = await self._run(query.window(None, 1))
        return found[0] if found else None

    async def last(self) -> M | None:
        """Last record by the queryset's ordering (primary key when unordered), or None."""
        if self._query.windowed:
            found = await self
            return found[-1] if found else None
        found = await self._run(self._query.reverse().window(None, 1))
        return found[0] if found else None

    async def aggregate(self, *positional: Aggregate, **aggregates: Aggregate) -> dict[str, Any]:
        """One dict of aggregate values over the (filtered, sliced) records."""
        return await self._query.aggregate(_aggregate_specs(positional, aggregates))

    async def update(self, **values: Any) -> int:
        """Updates every matching row in one statement; values may be ``F()`` expressions.
        Returns the number of rows changed."""
        return await self._query.update(values)

    async def delete(self) -> int:
        """Deletes every matching row in one statement (related rows follow ``on_delete``);
        returns the number of rows deleted."""
        return await self._query.delete()

    async def bulk_create(
        self,
        objects: Iterable[M],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
    ) -> list[M]:
        """Inserts all objects atomically and sets their primary keys. With
        ``ignore_conflicts=True`` rows violating a unique constraint are skipped. Returns the
        objects."""
        objects = objects if isinstance(objects, list) else list(objects)
        self._check_instances(objects)
        await _ferro.bulk_insert(self.model._meta, objects, batch_size, ignore_conflicts)
        return objects

    async def bulk_update(
        self, objects: Iterable[M], fields: Iterable[str], batch_size: int | None = None
    ) -> int:
        """Writes only ``fields`` of each (saved) object, atomically; returns rows updated."""
        objects = list(objects)
        self._check_instances(objects)
        return await _ferro.bulk_update(self.model._meta, objects, list(fields), batch_size)

    def _check_instances(self, objects: list[Any]) -> None:
        for obj in objects:
            if type(obj) is not self.model:
                raise TypeError(
                    f"expected {self.model.__name__} instances, got {type(obj).__name__}"
                )

    def sql(self) -> tuple[str, list[Any]]:
        """The SELECT this queryset runs, as ``(sql, params)``; values are never in the text."""
        return self._query.sql()

    async def get(self, *conditions: Q, **lookups: Any) -> M:
        """The single matching record; ``DoesNotExist`` / ``MultipleObjectsReturned`` otherwise."""
        query = self._filtered(conditions, lookups, False)._query
        matches = await self._run(query.window(None, 2))
        if not matches:
            raise self.model.DoesNotExist(f"{self.model.__name__} matching query does not exist")
        if len(matches) > 1:
            raise self.model.MultipleObjectsReturned(
                f"get() returned more than one {self.model.__name__}"
            )
        return matches[0]

    async def get_or_404(self, *conditions: Q, **lookups: Any) -> M:
        """Like :meth:`get`, but a missing record becomes an HTTP 404 response."""
        try:
            return await self.get(*conditions, **lookups)
        except self.model.DoesNotExist:
            raise HTTPException(404, f"{self.model.__name__} not found") from None

    async def create(self, **values: Any) -> M:
        """Builds an instance with ``values`` (defaults for the rest), saves it and returns it."""
        instance = self.model(**values)
        await instance.save()
        return instance

    def __repr__(self) -> str:
        return f"<QuerySet {self.model.__name__}>"
