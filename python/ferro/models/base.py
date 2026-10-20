"""``Model`` and its metaclass."""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, Any, ClassVar

from ferro import _ferro
from ferro.models.exceptions import FieldError, MultipleObjectsReturned, ObjectDoesNotExist
from ferro.models.fields import Field
from ferro.models.relations import (
    CASCADE,
    ForeignKey,
    KeyAttribute,
    ManyToMany,
    ReverseForeignKey,
    ReverseManyToMany,
    ReverseOneToOne,
    prefetch_related_objects,
)

if TYPE_CHECKING:
    from ferro.models.queryset import QuerySet

__all__ = ["Model", "ModelBase", "registered_model_classes"]

_REGISTRY: dict[str, type] = {}
# Models waiting for a related model that is not defined yet (string references).
_PENDING: list[type] = []
# Registered many-to-many relations waiting for their custom link model (`through=`).
_PENDING_LINKS: list[tuple[type, ManyToMany]] = []


def registered_model_classes() -> list[type]:
    """Model classes defined in this process (latest definition per qualified name)."""
    return list(_REGISTRY.values())


_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def snake_case(name: str) -> str:
    """``OrderItem`` → ``order_item``; ``HTTPLog`` → ``http_log``."""
    return _BOUNDARY.sub("_", name).lower()


def _resolve(owner: type, to: Any) -> type | None:
    """The model class a relation points at, or None while it is not registered yet."""
    if to == "self":
        return owner
    if isinstance(to, str):
        if to in (owner.__name__, owner.__qualname__, f"{owner.__module__}.{owner.__qualname__}"):
            return owner
        if to in _REGISTRY:
            return _REGISTRY[to]
        same_module = f"{owner.__module__}.{to}"
        if same_module in _REGISTRY:
            return _REGISTRY[same_module]
        matches = [cls for cls in _REGISTRY.values() if to in (cls.__name__, cls.__qualname__)]
        if len(matches) > 1:
            raise FieldError(f"{owner.__name__}: model name '{to}' is ambiguous; use 'module.{to}'")
        return matches[0] if matches else None
    if isinstance(to, ModelBase) and "_meta" in to.__dict__:
        return to
    if isinstance(to, ModelBase):
        return None  # defined but itself waiting for a related model
    raise FieldError(f"{owner.__name__}: a relation needs a model class or name, got {to!r}")


def _unready(model: type) -> FieldError:
    missing = [
        str(r.to if isinstance(r.to, str) else r.to.__name__)
        for r in _relations(model)
        if _resolve(model, r.to) is None
    ]
    return FieldError(
        f"{model.__name__} cannot be used yet: related model "
        f"{', '.join(repr(m) for m in missing)} is not defined"
    )


class _Unready:
    """Stands in for `objects` while a model waits for related models."""

    def __init__(self, model: type) -> None:
        self._model = model

    def __getattr__(self, name: str) -> Any:
        raise _unready(self._model)


def _abstract_error(model: type) -> FieldError:
    return FieldError(
        f"{model.__name__} is abstract (Meta.abstract = True): it has no table and cannot be "
        "queried or instantiated; use a model inheriting from it"
    )


class _Abstract:
    """Stands in for `objects` on an abstract model."""

    def __init__(self, model: type) -> None:
        self._model = model

    def __getattr__(self, name: str) -> Any:
        raise _abstract_error(self._model)


def _relations(cls: type) -> list[Any]:
    return [*cls._relation_fields.values(), *cls._many_to_many.values()]


def _install(target: type, name: str, descriptor: Any, source: str) -> None:
    existing = target.__dict__.get(name)
    if existing is not None and not isinstance(
        existing, (ReverseForeignKey, ReverseOneToOne, ReverseManyToMany)
    ):
        raise FieldError(
            f"{source}: reverse accessor '{name}' clashes with {target.__name__}.{name}; set "
            f"related_name"
        )
    setattr(target, name, descriptor)


def _options(cls: type) -> tuple[Any, Any]:
    """``(own Meta, effective Meta)``: a model without its own ``Meta`` inherits the one of its
    first abstract base that has one."""
    own = cls.__dict__.get("Meta")
    if own is not None:
        return own, own
    for klass in cls.__mro__[1:]:
        if "Meta" in klass.__dict__:
            return None, klass.__dict__["Meta"]
    return None, None


def _register(cls: type) -> None:
    """Registers a model whose related models all exist."""
    for relation in _relations(cls):
        relation.target = _resolve(cls, relation.to)
    own, options = _options(cls)
    table = getattr(own, "table", None) or snake_case(cls.__name__)
    unique_together = [list(group) for group in getattr(options, "unique_together", ())]
    qualname = f"{cls.__module__}.{cls.__qualname__}"
    cls._meta = _ferro.register_model(
        cls,
        qualname,
        cls.__name__,
        table,
        [f.spec() for f in cls._fields.values()],
        unique_together,
    )
    _REGISTRY[qualname] = cls
    attnames = cls._meta.attnames
    cls._attnames = attnames
    cls._accepted = frozenset(attnames) | frozenset(cls._fields) | {"pk"}

    from ferro.models.queryset import QuerySet

    cls.objects = QuerySet(cls)
    for field in cls._relation_fields.values():
        setattr(cls, field.attname, KeyAttribute(field))
        accessor = field.reverse_accessor_name()
        if accessor is not None:
            descriptor = (ReverseOneToOne if field.one_to_one else ReverseForeignKey)(
                field, accessor
            )
            _install(field.target, accessor, descriptor, f"{cls.__name__}.{field.name}")
    for relation in cls._many_to_many.values():
        if relation.auto_created:
            _link_model(cls, relation)
        else:
            _PENDING_LINKS.append((cls, relation))


def _link_keys(cls: type, relation: ManyToMany, through: type) -> tuple[str, str]:
    """Names of the link model's foreign keys to the source and the target."""
    source, target = cls, relation.target
    where = f"{cls.__name__}.{relation.name}"
    keys = list(through._relation_fields.values())
    if relation.through_fields is not None:
        for name, side in zip(relation.through_fields, (source, target), strict=True):
            field = through._relation_fields.get(name)
            if field is None or field.target is not side:
                raise FieldError(
                    f"{where}: through_fields names '{name}', which is not a foreign key from "
                    f"{through.__name__} to {side.__name__}"
                )
        return relation.through_fields
    to_source = [f.name for f in keys if f.target is source]
    if source is target:
        if len(to_source) != 2:
            raise FieldError(
                f"{where}: the link model {through.__name__} needs exactly two foreign keys to "
                f"{source.__name__}; name them with through_fields=(source, target)"
            )
        return to_source[0], to_source[1]
    to_target = [f.name for f in keys if f.target is target]
    for side, found in ((source, to_source), (target, to_target)):
        if not found:
            raise FieldError(
                f"{where}: the link model {through.__name__} has no foreign key to {side.__name__}"
            )
        if len(found) > 1:
            raise FieldError(
                f"{where}: the link model {through.__name__} has several foreign keys to "
                f"{side.__name__}; name the two to use with through_fields=(source, target)"
            )
    return to_source[0], to_target[0]


def _link_custom(cls: type, relation: ManyToMany, through: type) -> None:
    """Registers a many-to-many relation through the developer's own link model."""
    if getattr(through, "_auto_created", False):
        raise FieldError(f"{cls.__name__}.{relation.name}: through= must name your own model")
    relation.from_name, relation.to_name = _link_keys(cls, relation, through)
    _ferro.register_many_to_many(
        cls._meta,
        relation.name,
        relation.target._meta,
        through._meta,
        relation.from_name,
        relation.to_name,
        relation.related_name,
    )
    relation.through = through
    _install(
        relation.target,
        relation.reverse_accessor_name(),
        ReverseManyToMany(relation, relation.reverse_accessor_name()),
        f"{cls.__name__}.{relation.name}",
    )


def _link_model(cls: type, relation: ManyToMany) -> None:
    """Creates the hidden link model of a many-to-many relation and registers the relation."""
    target = relation.target
    source_name, target_name = snake_case(cls.__name__), snake_case(target.__name__)
    if source_name == target_name:
        source_name, target_name = f"from_{source_name}", f"to_{target_name}"
    relation.from_name, relation.to_name = source_name, target_name
    meta = type(
        "Meta",
        (),
        {
            "table": f"{cls._meta.table}_{relation.name}",
            "unique_together": [(source_name, target_name)],
        },
    )
    attrs = {
        "__module__": cls.__module__,
        "__qualname__": f"{cls.__qualname__}_{relation.name}",
        "_auto_created": True,
        "Meta": meta,
        source_name: ForeignKey(cls, CASCADE, related_name="+"),
        target_name: ForeignKey(target, CASCADE, related_name="+"),
    }
    relation.through = ModelBase(f"{cls.__name__}_{relation.name}", (Model,), attrs)
    _ferro.register_many_to_many(
        cls._meta,
        relation.name,
        target._meta,
        relation.through._meta,
        source_name,
        target_name,
        relation.related_name,
    )
    _install(
        target,
        relation.reverse_accessor_name(),
        ReverseManyToMany(relation, relation.reverse_accessor_name()),
        f"{cls.__name__}.{relation.name}",
    )


def _register_ready() -> None:
    """Registers every pending model whose related models now exist, and every many-to-many
    relation whose link model now exists (repeatedly, since one registration can unblock
    another)."""
    progress = True
    while progress:
        progress = False
        for cls in list(_PENDING):
            if cls not in _PENDING:
                continue  # registered by a nested call
            if all(_resolve(cls, r.to) is not None for r in _relations(cls)):
                _PENDING.remove(cls)
                _register(cls)
                progress = True
        for entry in list(_PENDING_LINKS):
            cls, relation = entry
            through = _resolve(cls, relation.through_ref)
            if through is not None and entry in _PENDING_LINKS:
                _PENDING_LINKS.remove(entry)  # also when it fails: report it once
                _link_custom(cls, relation, through)
                progress = True


def _inherit(cls: type, parents: list[type], namespace: dict[str, Any]) -> None:
    """Collects the fields and many-to-many relations of abstract bases (parents first) and the
    class's own; inherited ones are copied so each model owns its fields. Redefining a name
    replaces the inherited field; setting it to anything else (``None``) removes it."""
    fields: dict[str, Any] = {}
    many: dict[str, ManyToMany] = {}
    for parent in parents:
        if "_fields" not in parent.__dict__:
            continue  # `Model`
        for name, field in parent._fields.items():
            fields.setdefault(name, field)
        for name, relation in parent._many_to_many.items():
            many.setdefault(name, relation)
    inherited = {id(value) for value in (*fields.values(), *many.values())}
    for name, value in namespace.items():
        if isinstance(value, Field):
            many.pop(name, None)
            fields[name] = value
        elif isinstance(value, ManyToMany):
            fields.pop(name, None)
            many[name] = value
        else:
            fields.pop(name, None)
            many.pop(name, None)
    concrete = not cls.__dict__["_abstract"]
    label = snake_case(cls.__name__)
    for group in (fields, many):
        for name, value in group.items():
            if id(value) in inherited:
                value = copy.copy(value)
                if isinstance(value, ManyToMany):
                    value.target = value.through = None
                    value.from_name = value.to_name = ""
                elif isinstance(value, ForeignKey):
                    value.target = None
                value.__set_name__(cls, name)
                setattr(cls, name, value)
                group[name] = value
            related = getattr(value, "related_name", None)
            if concrete and related and "%(class)s" in related:
                value.related_name = related.replace("%(class)s", label)
    for relation in many.values():
        if not concrete and not relation.auto_created:
            raise FieldError(
                f"{cls.__name__}.{relation.name}: a many-to-many with through= cannot be "
                "declared on an abstract model; declare it on each concrete model"
            )
    cls._fields = fields
    cls._relation_fields = {n: f for n, f in fields.items() if isinstance(f, ForeignKey)}
    cls._many_to_many = many
    cls._defaults = [(n, f) for n, f in fields.items() if f.has_default()]


class ModelBase(type):
    """Collects fields, registers the model with the native core (once its related models
    exist) and attaches the manager, per-model exceptions and relation accessors."""

    def __new__(mcs, name: str, bases: tuple[type, ...], namespace: dict[str, Any], **kwargs: Any):
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)
        parents = [base for base in bases if isinstance(base, ModelBase)]
        if not parents:
            return cls  # `Model` itself
        for parent in parents:
            if "_fields" in parent.__dict__ and not parent.__dict__.get("_abstract"):
                raise FieldError(
                    f"{name}: inheriting from the concrete model {parent.__name__} is not "
                    "supported; move the shared fields to an abstract base (class Meta: "
                    "abstract = True) and inherit both models from it"
                )
        own_meta = namespace.get("Meta")
        cls._abstract = bool(own_meta is not None and vars(own_meta).get("abstract", False))
        _inherit(cls, parents, namespace)
        if cls._abstract:
            cls.objects = _Abstract(cls)
            return cls

        def exception(base: type[Exception], label: str) -> type[Exception]:
            return type(
                label,
                (base,),
                {"__module__": cls.__module__, "__qualname__": f"{cls.__qualname__}.{label}"},
            )

        cls.DoesNotExist = exception(ObjectDoesNotExist, "DoesNotExist")
        cls.MultipleObjectsReturned = exception(MultipleObjectsReturned, "MultipleObjectsReturned")
        cls.objects = _Unready(cls)
        _PENDING.append(cls)
        _register_ready()
        return cls


class Model(metaclass=ModelBase):
    """Base class for models::

    class Product(models.Model):
        name = models.String(max_length=200)
        price = models.Decimal(max_digits=10, decimal_places=2)
        category = models.ForeignKey(Category, on_delete=models.CASCADE)
    """

    _meta: ClassVar[_ferro.ModelMeta]
    _fields: ClassVar[dict[str, Field]]
    _relation_fields: ClassVar[dict[str, ForeignKey]]
    _many_to_many: ClassVar[dict[str, ManyToMany]]
    _attnames: ClassVar[list[str]]
    _accepted: ClassVar[frozenset[str]]
    _defaults: ClassVar[list[tuple[str, Field]]]
    _abstract: ClassVar[bool]
    objects: ClassVar[QuerySet[Any]]
    DoesNotExist: ClassVar[type[ObjectDoesNotExist]]
    MultipleObjectsReturned: ClassVar[type[MultipleObjectsReturned]]

    def __init__(self, **values: Any) -> None:
        cls = type(self)
        if "_meta" not in cls.__dict__:
            if cls.__dict__.get("_abstract"):
                raise _abstract_error(cls)
            raise _unready(cls)
        meta = cls._meta
        unknown = [key for key in values if key not in cls._accepted]
        if unknown:
            if unknown[0] in cls._many_to_many:
                raise FieldError(
                    f"{meta.label}.{unknown[0]} is many-to-many; save the object, then use "
                    f"`await obj.{unknown[0]}.set([...])`"
                )
            raise FieldError(
                f"{meta.label} has no field '{unknown[0]}'; fields: {', '.join(meta.field_names)}"
            )
        state = self.__dict__
        for name in cls._attnames:
            state[name] = None
        for _, field in cls._defaults:
            state[getattr(field, "attname", field.name)] = field.get_default()
        if "pk" in values:
            values[meta.pk_name] = values.pop("pk")
        state["_persisted"] = False
        if cls._relation_fields:
            relations = cls._relation_fields
            for key, value in values.items():
                if key in relations:
                    setattr(self, key, value)
                else:
                    state[key] = value
        else:
            state.update(values)

    @property
    def pk(self) -> Any:
        return self.__dict__.get(type(self)._meta.pk_name)

    @pk.setter
    def pk(self, value: Any) -> None:
        self.__dict__[type(self)._meta.pk_name] = value

    async def save(self) -> None:
        """Inserts a new row or updates the existing one. Sends ``pre_save`` and ``post_save``
        (``ferro.signals``) when receivers are connected."""
        from ferro import signals

        cls = type(self)
        meta = cls._meta
        existing = self._persisted and self.pk is not None
        if signals.pre_save._receivers:
            await signals.pre_save.send(cls, instance=self, adding=not existing)
        created = not (existing and await _ferro.update_instance(meta, self))
        if created:
            await _ferro.insert(meta, self)
        if signals.post_save._receivers:
            await signals.post_save.send(cls, instance=self, created=created)

    async def delete(self) -> None:
        """Deletes the row (related rows follow their ``on_delete`` rule in the database, without
        signals). An automatic primary key is reset to None. Sends ``pre_delete`` and
        ``post_delete``."""
        from ferro import signals

        cls = type(self)
        meta = cls._meta
        if signals.pre_delete._receivers:
            await signals.pre_delete.send(cls, instance=self)
        await _ferro.delete_instance(meta, self)
        self.__dict__["_persisted"] = False
        if signals.post_delete._receivers:
            await signals.post_delete.send(cls, instance=self)
        if meta.auto_pk:
            self.pk = None

    async def refresh(self) -> None:
        """Reloads every field from the database (``DoesNotExist`` if the row is gone) and forgets
        loaded related objects."""
        fresh = await type(self).objects.get(pk=self.pk)
        self.__dict__.pop("_related", None)
        self.__dict__.update(fresh.__dict__)

    async def fetch_related(self, *paths: str) -> Model:
        """Loads related objects (``"category"``, ``"items__product"``, ``"tags"``) so they can be
        read as attributes. Returns the instance."""
        await prefetch_related_objects([self], *paths)
        return self

    def to_dict(self) -> dict[str, Any]:
        """Field values by attribute name (relations as ``<name>_id``), ready to return from a
        handler as JSON."""
        state = self.__dict__
        return {name: state.get(name) for name in type(self)._attnames}

    def __repr__(self) -> str:
        return f"<{type(self).__name__} pk={self.pk!r}>"

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return self.pk is not None and self.pk == other.pk  # type: ignore[attr-defined]

    def __hash__(self) -> int:
        if self.pk is None:
            raise TypeError("model instances without a primary key are unhashable")
        return hash((type(self), self.pk))
