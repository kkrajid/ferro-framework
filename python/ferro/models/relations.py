"""Relations: ``ForeignKey``, ``OneToOne`` and ``ManyToMany``, their descriptors, the managers on
both sides and ``prefetch_related``.

```python
class Product(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="products")
    tags = models.ManyToMany(Tag, related_name="products")
```

Reading ``product.category`` never queries: the object must have been loaded with
``select_related``/``prefetch_related``/``fetch_related`` (or assigned); otherwise
``RelatedNotLoaded`` is raised. ``product.category_id`` is always available.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

from ferro.models.exceptions import FieldError, RelatedNotLoaded
from ferro.models.fields import NOT_PROVIDED, Field

if TYPE_CHECKING:
    from ferro.models.base import Model
    from ferro.models.queryset import QuerySet

__all__ = [
    "CASCADE",
    "DO_NOTHING",
    "PROTECT",
    "SET_NULL",
    "ForeignKey",
    "ManyToMany",
    "OneToOne",
    "prefetch_related_objects",
]

CASCADE = "cascade"
"""Delete the referencing rows too."""
SET_NULL = "set_null"
"""Set the key to NULL (needs ``null=True``)."""
PROTECT = "protect"
"""Refuse the delete with ``ProtectedError``."""
DO_NOTHING = "do_nothing"
"""Leave it to the database constraint (the delete fails while rows refer to it)."""

_ON_DELETE = (CASCADE, SET_NULL, PROTECT, DO_NOTHING)


def _related_cache(instance: Any) -> dict[str, Any]:
    state = instance.__dict__
    cache = state.get("_related")
    if cache is None:
        cache = state["_related"] = {}
    return cache


def _target_label(to: Any) -> str:
    return to if isinstance(to, str) else to.__name__


class ForeignKey(Field):
    """A many-to-one relation storing the target's primary key in ``<name>_id``.

    ``to``: a model class, ``"self"``, or a model name (``"Category"``, ``"shop.models.Category"``)
    resolved once that model is defined. ``on_delete``: ``CASCADE``, ``SET_NULL``, ``PROTECT`` or
    ``DO_NOTHING``. ``related_name`` names the reverse accessor and lookups on the target
    (``"+"`` for none).
    """

    kind = "ForeignKey"
    one_to_one = False

    def __init__(
        self,
        to: Any,
        on_delete: str,
        *,
        related_name: str | None = None,
        null: bool = False,
        column: str | None = None,
        default: Any = NOT_PROVIDED,
    ) -> None:
        if on_delete not in _ON_DELETE:
            raise FieldError(
                "on_delete must be one of CASCADE, SET_NULL, PROTECT, DO_NOTHING; "
                f"got {on_delete!r}"
            )
        super().__init__(null=null, default=default, column=column)
        self.to = to
        self.on_delete = on_delete
        self.related_name = related_name
        self.model: type[Model] | None = None
        self.target: type[Model] | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        super().__set_name__(owner, name)
        self.model = owner

    @property
    def attname(self) -> str:
        return f"{self.name}_id"

    def spec(self) -> dict[str, Any]:
        spec = super().spec()
        assert self.target is not None, "relation targets are resolved before registration"
        spec.update(
            target=self.target._meta.qualname if self.target is not self.model else "self",
            on_delete=self.on_delete,
            related_name=self.related_name,
        )
        return spec

    # Reverse names (Django's rule with Ferro's snake_case model names).
    def reverse_query_name(self) -> str | None:
        from ferro.models.base import snake_case

        if self.related_name == "+":
            return None
        return self.related_name or snake_case(self.model.__name__)  # type: ignore[union-attr]

    def reverse_accessor_name(self) -> str | None:
        from ferro.models.base import snake_case

        if self.related_name == "+":
            return None
        if self.related_name:
            return self.related_name
        name = snake_case(self.model.__name__)  # type: ignore[union-attr]
        return name if self.one_to_one else f"{name}_set"

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        cache = instance.__dict__.get("_related")
        if cache is not None and self.name in cache:
            return cache[self.name]
        if instance.__dict__.get(self.attname) is None:
            return None
        raise RelatedNotLoaded(
            f"{type(instance).__name__}.{self.name} is not loaded; use "
            f".select_related({self.name!r}) on the query or "
            f"`await obj.fetch_related({self.name!r})`"
        )

    def __set__(self, instance: Any, value: Any) -> None:
        from ferro.models.base import Model

        if value is None:
            instance.__dict__[self.attname] = None
            _related_cache(instance)[self.name] = None
            return
        if not isinstance(value, Model):
            raise TypeError(
                f"{type(instance).__name__}.{self.name} must be a "
                f"{_target_label(self.target or self.to)} instance or None; set "
                f"{self.attname} to assign a key"
            )
        if self.target is not None and not isinstance(value, self.target):
            raise TypeError(
                f"{type(instance).__name__}.{self.name} must be a {self.target.__name__} "
                f"instance, got {type(value).__name__}"
            )
        if value.pk is None:
            raise ValueError(
                f"{type(value).__name__} instance must be saved before it is assigned to "
                f"{type(instance).__name__}.{self.name}"
            )
        instance.__dict__[self.attname] = value.pk
        _related_cache(instance)[self.name] = value

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name or '(unbound)'} -> {_target_label(self.to)}>"


class OneToOne(ForeignKey):
    """A one-to-one relation: a unique foreign key. The reverse side is a single object."""

    kind = "OneToOne"
    one_to_one = True


class KeyAttribute:
    """``<name>_id``: the stored key. Changing it forgets the cached related object."""

    def __init__(self, field: ForeignKey) -> None:
        self.field = field

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        return instance.__dict__.get(self.field.attname)

    def __set__(self, instance: Any, value: Any) -> None:
        state = instance.__dict__
        if state.get(self.field.attname) != value:
            cache = state.get("_related")
            if cache is not None:
                cache.pop(self.field.name, None)
        state[self.field.attname] = value


def _require_pk(instance: Any, what: str) -> Any:
    if instance.pk is None:
        raise ValueError(f"save the {type(instance).__name__} before using {what}")
    return instance.pk


class _Delegating:
    """Queryset methods of a manager: each call starts from ``all()``."""

    instance: Any
    accessor: str

    def all(self) -> QuerySet[Any]:
        raise NotImplementedError

    def __iter__(self) -> Iterator[Any]:
        """The related objects loaded with ``prefetch_related``/``fetch_related`` (iterating never
        queries, so response models can read them); ``RelatedNotLoaded`` otherwise."""
        cache = self.instance.__dict__.get("_related")
        if cache is None or self.accessor not in cache:
            raise RelatedNotLoaded(
                f"{type(self.instance).__name__}.{self.accessor} is not loaded; use "
                f".prefetch_related({self.accessor!r}) on the query or "
                f"`await obj.fetch_related({self.accessor!r})`"
            )
        return iter(list(cache[self.accessor]))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.all(), name)


class RelatedManager(_Delegating):
    """``category.products``: the rows whose foreign key points at ``instance``."""

    def __init__(self, instance: Any, field: ForeignKey, accessor: str) -> None:
        self.instance = instance
        self.field = field
        self.accessor = accessor
        self.model: type[Model] = field.model  # type: ignore[assignment]

    def all(self) -> QuerySet[Any]:
        pk = _require_pk(self.instance, self.accessor)
        queryset = self.model.objects.filter(**{self.field.name: pk})
        cache = self.instance.__dict__.get("_related")
        if cache is not None and self.accessor in cache:
            queryset._result_cache = list(cache[self.accessor])
        return queryset

    async def create(self, **values: Any) -> Any:
        values[self.field.name] = self.instance
        _require_pk(self.instance, self.accessor)
        return await self.model.objects.create(**values)

    def _check(self, objects: Iterable[Any]) -> list[Any]:
        objects = list(objects)
        for obj in objects:
            if type(obj) is not self.model:
                raise TypeError(
                    f"{self.accessor}: expected {self.model.__name__} instances, "
                    f"got {type(obj).__name__}"
                )
            if obj.pk is None:
                raise ValueError(f"{self.accessor}: {obj!r} is not saved yet")
        return objects

    def _nullable(self, method: str) -> None:
        if not self.field.null:
            raise TypeError(
                f"{self.accessor}.{method}() needs a nullable foreign key "
                f"({self.model.__name__}.{self.field.name} has null=False); delete the rows instead"
            )

    def _forget(self) -> None:
        cache = self.instance.__dict__.get("_related")
        if cache is not None:
            cache.pop(self.accessor, None)

    async def add(self, *objects: Any) -> None:
        """Points the saved ``objects`` at this record (one UPDATE)."""
        pk = _require_pk(self.instance, self.accessor)
        objects = self._check(objects)
        if objects:
            await self.model.objects.filter(pk__in=[o.pk for o in objects]).update(
                **{self.field.name: pk}
            )
            for obj in objects:
                obj.__dict__[self.field.attname] = pk
                obj.__dict__.get("_related", {}).pop(self.field.name, None)
        self._forget()

    async def remove(self, *objects: Any) -> None:
        """Unlinks ``objects`` from this record (their key becomes NULL)."""
        self._nullable("remove")
        pk = _require_pk(self.instance, self.accessor)
        objects = self._check(objects)
        if objects:
            await self.model.objects.filter(
                pk__in=[o.pk for o in objects], **{self.field.name: pk}
            ).update(**{self.field.name: None})
            for obj in objects:
                obj.__dict__[self.field.attname] = None
        self._forget()

    async def clear(self) -> None:
        """Unlinks every related row (their key becomes NULL)."""
        self._nullable("clear")
        pk = _require_pk(self.instance, self.accessor)
        await self.model.objects.filter(**{self.field.name: pk}).update(**{self.field.name: None})
        self._forget()

    async def set(self, objects: Iterable[Any]) -> None:
        """Makes the related rows exactly ``objects`` (one transaction): others are unlinked
        (nullable keys) and the given ones added."""
        from ferro.db import transaction

        self._nullable("set")
        pk = _require_pk(self.instance, self.accessor)
        objects = self._check(objects)
        async with transaction():
            await (
                self.model.objects.filter(**{self.field.name: pk})
                .exclude(pk__in=[o.pk for o in objects])
                .update(**{self.field.name: None})
            )
            await self.add(*objects)

    def __repr__(self) -> str:
        return f"<RelatedManager {type(self.instance).__name__}.{self.accessor}>"


class ReverseForeignKey:
    """Installed on the target model as ``<model>_set`` / ``related_name``."""

    def __init__(self, field: ForeignKey, accessor: str) -> None:
        self.field = field
        self.accessor = accessor

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        return RelatedManager(instance, self.field, self.accessor)

    def __set__(self, instance: Any, value: Any) -> None:
        raise TypeError(
            f"{self.accessor} cannot be assigned; set {self.field.name} on each "
            f"{self.field.model.__name__} instead"  # type: ignore[union-attr]
        )


class ReverseOneToOne:
    """``customer.profile``: the single row pointing here (loaded with ``fetch_related`` or
    ``prefetch_related``)."""

    def __init__(self, field: ForeignKey, accessor: str) -> None:
        self.field = field
        self.accessor = accessor

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        cache = instance.__dict__.get("_related")
        if cache is None or self.accessor not in cache:
            raise RelatedNotLoaded(
                f"{type(instance).__name__}.{self.accessor} is not loaded; use "
                f".prefetch_related({self.accessor!r}) or "
                f"`await obj.fetch_related({self.accessor!r})`"
            )
        related = cache[self.accessor]
        if related is None:
            source = self.field.model
            raise source.DoesNotExist(  # type: ignore[union-attr]
                f"{type(instance).__name__} has no {source.__name__}"  # type: ignore[union-attr]
            )
        return related

    def __set__(self, instance: Any, value: Any) -> None:
        raise TypeError(f"{self.accessor} cannot be assigned; set {self.field.name} instead")


class ManyToMany:
    """A many-to-many relation through an automatic link table ``<table>_<name>``, or through
    your own link model when it carries data about each link::

        class Band(models.Model):
            members = models.ManyToMany(Musician, through="Membership", related_name="bands")

        class Membership(models.Model):
            band = models.ForeignKey(Band, on_delete=models.CASCADE)
            musician = models.ForeignKey(Musician, on_delete=models.CASCADE)
            role = models.String(max_length=20)

        await band.members.add(ann, through_defaults={"role": "guitar"})

    The link model needs one foreign key to each side; ``through_fields=(source, target)``
    names them when there are more (or for a relation to ``"self"``). Both sides get a manager
    with ``add``, ``remove``, ``clear``, ``set`` and queryset methods.
    """

    def __init__(
        self,
        to: Any,
        *,
        related_name: str | None = None,
        through: Any = None,
        through_fields: tuple[str, str] | None = None,
    ) -> None:
        if related_name == "+":
            raise FieldError("ManyToMany needs a reverse name; related_name='+' is not supported")
        if through_fields is not None:
            if through is None:
                raise FieldError("ManyToMany: through_fields needs a link model (through=...)")
            if (
                not isinstance(through_fields, (tuple, list))
                or len(through_fields) != 2
                or not all(isinstance(name, str) for name in through_fields)
            ):
                raise FieldError(
                    "ManyToMany: through_fields must be two field names (source, target), got "
                    f"{through_fields!r}"
                )
        self.to = to
        self.related_name = related_name
        self.through_ref = through
        self.through_fields = tuple(through_fields) if through_fields is not None else None
        self.name = ""
        self.model: type[Model] | None = None
        self.target: type[Model] | None = None
        self.through: type[Model] | None = None
        self.from_name = ""
        self.to_name = ""

    @property
    def auto_created(self) -> bool:
        """Whether the link model is the automatic one (no ``through=``)."""
        return self.through_ref is None

    def link(self) -> type[Model]:
        """The link model; a clear error while a custom one is not defined yet."""
        if self.through is None:
            owner = self.model.__name__ if self.model is not None else "ManyToMany"
            raise FieldError(
                f"{owner}.{self.name} cannot be used yet: link model "
                f"{_target_label(self.through_ref or self.to)!r} is not defined"
            )
        return self.through

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name
        self.model = owner

    def reverse_query_name(self) -> str:
        from ferro.models.base import snake_case

        return self.related_name or snake_case(self.model.__name__)  # type: ignore[union-attr]

    def reverse_accessor_name(self) -> str:
        from ferro.models.base import snake_case

        return self.related_name or f"{snake_case(self.model.__name__)}_set"  # type: ignore[union-attr]

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        return ManyRelatedManager(instance, self, reverse=False)

    def __set__(self, instance: Any, value: Any) -> None:
        raise TypeError(f"use `await obj.{self.name}.set([...])` to change {self.name}")

    def __repr__(self) -> str:
        return f"<ManyToMany {self.name or '(unbound)'} -> {_target_label(self.to)}>"


class ReverseManyToMany:
    def __init__(self, relation: ManyToMany, accessor: str) -> None:
        self.relation = relation
        self.accessor = accessor

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        return ManyRelatedManager(instance, self.relation, reverse=True)

    def __set__(self, instance: Any, value: Any) -> None:
        raise TypeError(f"use `await obj.{self.accessor}.set([...])` to change {self.accessor}")


class ManyRelatedManager(_Delegating):
    """One side of a many-to-many relation for one instance."""

    def __init__(self, instance: Any, relation: ManyToMany, reverse: bool) -> None:
        self.instance = instance
        self.relation = relation
        self.through: type[Model] = relation.link()
        if reverse:
            self.model: type[Model] = relation.model  # type: ignore[assignment]
            self.lookup = relation.name
            self.accessor = relation.reverse_accessor_name()
            self.own_side, self.other_side = relation.to_name, relation.from_name
        else:
            self.model = relation.target  # type: ignore[assignment]
            self.lookup = relation.reverse_query_name()
            self.accessor = relation.name
            self.own_side, self.other_side = relation.from_name, relation.to_name

    def all(self) -> QuerySet[Any]:
        pk = _require_pk(self.instance, self.accessor)
        queryset = self.model.objects.filter(**{self.lookup: pk})
        cache = self.instance.__dict__.get("_related")
        if cache is not None and self.accessor in cache:
            queryset._result_cache = list(cache[self.accessor])
        return queryset

    def _keys(self, objects: Iterable[Any]) -> list[Any]:
        from ferro.models.base import Model

        keys = []
        for obj in objects:
            if isinstance(obj, Model):
                if not isinstance(obj, self.model):
                    raise TypeError(
                        f"{self.accessor} takes {self.model.__name__} instances or keys, got "
                        f"{type(obj).__name__}"
                    )
                keys.append(_require_pk(obj, self.accessor))
            else:
                keys.append(obj)
        return keys

    def _forget(self) -> None:
        cache = self.instance.__dict__.get("_related")
        if cache is not None:
            cache.pop(self.accessor, None)

    def _link_values(self, through_defaults: dict[str, Any] | None) -> dict[str, Any]:
        """Values for the extra fields of a custom link model; required ones must be given."""
        if self.relation.auto_created:
            if through_defaults:
                raise FieldError(
                    f"{self.accessor}: through_defaults needs a custom link model "
                    "(ManyToMany(..., through=...))"
                )
            return {}
        values = dict(through_defaults or {})
        sides = {self.own_side, self.other_side}
        missing = [
            name
            for name, field in self.through._fields.items()
            if name not in sides
            and name not in values
            and not field.has_default()
            and not field.null
            and not field.primary_key
        ]
        if missing:
            raise FieldError(
                f"{self.through.__name__}.{missing[0]} needs a value for each link; pass "
                f"through_defaults={{{missing[0]!r}: ...}} to {self.accessor}.add() or .set()"
            )
        return values

    async def add(self, *objects: Any, through_defaults: dict[str, Any] | None = None) -> None:
        """Links the objects (instances or keys); existing links are kept once.
        ``through_defaults`` fills the extra fields of a custom link model."""
        pk = _require_pk(self.instance, self.accessor)
        keys = list(dict.fromkeys(self._keys(objects)))
        values = self._link_values(through_defaults)
        if not keys:
            return
        await self._changed("pre_add", set(keys))
        own, other = self.own_side, self.other_side
        if self.relation.auto_created:
            links = [self.through(**{f"{own}_id": pk, f"{other}_id": key}) for key in keys]
            await self.through.objects.bulk_create(links, ignore_conflicts=True)
        else:
            # A custom link model need not be unique per pair, so look before inserting.
            linked = set(
                await self.through.objects.filter(**{own: pk, f"{other}__in": keys}).values_list(
                    other, flat=True
                )
            )
            links = [
                self.through(**values, **{f"{own}_id": pk, f"{other}_id": key})
                for key in keys
                if key not in linked
            ]
            if links:
                await self.through.objects.bulk_create(links)
        self._forget()
        await self._changed("post_add", set(keys))

    async def _changed(self, action: str, keys: set[Any] | None) -> None:
        from ferro import signals

        if signals.m2m_changed._receivers:
            await signals.m2m_changed.send(
                self.through,
                instance=self.instance,
                action=action,
                model=self.model,
                pk_set=keys,
                reverse=self.own_side == self.relation.to_name,
            )

    async def remove(self, *objects: Any) -> None:
        pk = _require_pk(self.instance, self.accessor)
        keys = self._keys(objects)
        if keys:
            await self._changed("pre_remove", set(keys))
            await self.through.objects.filter(
                **{self.own_side: pk, f"{self.other_side}__in": keys}
            ).delete()
            self._forget()
            await self._changed("post_remove", set(keys))
        self._forget()

    async def clear(self) -> None:
        pk = _require_pk(self.instance, self.accessor)
        await self._changed("pre_clear", None)
        await self.through.objects.filter(**{self.own_side: pk}).delete()
        self._forget()
        await self._changed("post_clear", None)

    async def set(
        self, objects: Iterable[Any], *, through_defaults: dict[str, Any] | None = None
    ) -> None:
        """Makes the linked set exactly ``objects`` (one transaction); new links get
        ``through_defaults``."""
        from ferro.db import transaction

        pk = _require_pk(self.instance, self.accessor)
        wanted = list(dict.fromkeys(self._keys(objects)))
        self._link_values(through_defaults)
        async with transaction():
            current = await self.through.objects.filter(**{self.own_side: pk}).values_list(
                self.other_side, flat=True
            )
            stale = [key for key in current if key not in set(wanted)]
            if stale:
                await self.remove(*stale)
            missing = [key for key in wanted if key not in set(current)]
            if missing:
                await self.add(*missing, through_defaults=through_defaults)
        self._forget()

    def __repr__(self) -> str:
        return f"<ManyRelatedManager {type(self.instance).__name__}.{self.accessor}>"


# --- prefetching ---------------------------------------------------------------------------


async def _prefetch_level(instances: list[Any], name: str) -> list[Any]:
    """Loads relation ``name`` for all ``instances`` (same class) and returns the related
    objects, for the next level."""
    if not instances:
        return []
    cls = type(instances[0])
    descriptor = getattr(cls, name, None)
    if isinstance(descriptor, ForeignKey):
        field = descriptor
        keys = {obj.__dict__.get(field.attname) for obj in instances} - {None}
        loaded: dict[Any, Any] = {}
        if keys:
            target = field.target
            for obj in await target.objects.filter(pk__in=list(keys)):  # type: ignore[union-attr]
                loaded[obj.pk] = obj
        for obj in instances:
            _related_cache(obj)[field.name] = loaded.get(obj.__dict__.get(field.attname))
        return list(loaded.values())
    if isinstance(descriptor, (ReverseForeignKey, ReverseOneToOne)):
        field = descriptor.field
        keys = [obj.pk for obj in instances]
        groups: dict[Any, list[Any]] = {key: [] for key in keys}
        related = await field.model.objects.filter(**{f"{field.name}__in": keys})  # type: ignore[union-attr]
        for obj in related:
            groups.setdefault(obj.__dict__.get(field.attname), []).append(obj)
        for obj in instances:
            found = groups.get(obj.pk, [])
            if isinstance(descriptor, ReverseOneToOne):
                _related_cache(obj)[descriptor.accessor] = found[0] if found else None
            else:
                _related_cache(obj)[descriptor.accessor] = found
        return list(related)
    if isinstance(descriptor, (ManyToMany, ReverseManyToMany)):
        relation = descriptor if isinstance(descriptor, ManyToMany) else descriptor.relation
        reverse = isinstance(descriptor, ReverseManyToMany)
        own, other = (
            (relation.to_name, relation.from_name)
            if reverse
            else (relation.from_name, relation.to_name)
        )
        model = relation.model if reverse else relation.target
        accessor = relation.reverse_accessor_name() if reverse else relation.name
        keys = [obj.pk for obj in instances]
        links = await relation.link().objects.filter(**{f"{own}__in": keys}).values_list(own, other)
        targets: dict[Any, Any] = {}
        wanted = {target for _, target in links}
        if wanted:
            for obj in await model.objects.filter(pk__in=list(wanted)):  # type: ignore[union-attr]
                targets[obj.pk] = obj
        groups = {key: [] for key in keys}
        for source, target in links:
            if target in targets:
                groups.setdefault(source, []).append(targets[target])
        for obj in instances:
            _related_cache(obj)[accessor] = groups.get(obj.pk, [])
        return list(targets.values())
    raise FieldError(f"{cls.__name__} has no relation '{name}' to prefetch")


async def prefetch_related_objects(instances: Iterable[Any], *paths: str) -> None:
    """Loads relations (``"category"``, ``"items__product"``, ``"tags"``) for many instances
    with one query per relation level (two for many-to-many)."""
    instances = list(instances)
    for path in paths:
        level = instances
        for name in path.split("__"):
            by_class: dict[type, list[Any]] = {}
            for obj in level:
                if obj is not None:
                    by_class.setdefault(type(obj), []).append(obj)
            following: list[Any] = []
            for group in by_class.values():
                following.extend(await _prefetch_level(group, name))
            level = following
