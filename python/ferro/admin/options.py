"""``ModelAdmin``: how one model appears in the admin."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ferro.exceptions import ConfigurationError

__all__ = ["ModelAdmin"]


@dataclass
class ModelAdmin:
    """Options for a registered model (all optional)::

    ModelAdmin(
        list_display=["name", "price", "category"],   # columns ("__str__" allowed)
        search_fields=["name", "category__name"],     # case-insensitive contains, OR-ed
        list_filter=["category", "active"],           # filter links
        ordering=["-price"],
        list_per_page=50,
        readonly_fields=["created"],
    )
    """

    list_display: Sequence[str] = ()
    search_fields: Sequence[str] = ()
    list_filter: Sequence[str] = ()
    ordering: Sequence[str] = ()
    list_per_page: int = 50
    readonly_fields: Sequence[str] = field(default_factory=tuple)

    def check(self, model: type) -> None:
        """Raises ``ConfigurationError`` for names the model does not have."""
        names = set(model._fields) | set(model._many_to_many) | {"pk", model._meta.pk_name}
        label = model.__name__

        def require(option: str, name: str, *, path: bool = False) -> None:
            head = name.split("__", 1)[0] if path else name
            if head not in names:
                raise ConfigurationError(
                    f"{label} admin: {option} names no field '{name}'; fields: "
                    f"{', '.join(model._meta.field_names)}"
                )

        for name in self.list_display:
            if name != "__str__":
                require("list_display", name)
        for name in self.search_fields:
            require("search_fields", name, path=True)
        for name in self.list_filter:
            require("list_filter", name)
        for name in self.ordering:
            require("ordering", name.lstrip("-"), path=True)
        for name in self.readonly_fields:
            require("readonly_fields", name)
        if not isinstance(self.list_per_page, int) or self.list_per_page < 1:
            raise ConfigurationError(f"{label} admin: list_per_page must be a positive integer")
