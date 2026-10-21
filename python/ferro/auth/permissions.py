"""Permissions (``<action>_<table>``) and handler decorators."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Iterable
from typing import Any

from ferro.auth.models import Permission
from ferro.exceptions import ConfigurationError, HTTPException

__all__ = [
    "DEFAULT_ACTIONS",
    "create_permissions",
    "login_required",
    "permission_required",
    "staff_required",
]

DEFAULT_ACTIONS = ("add", "change", "delete", "view")


async def create_permissions(models: Iterable[type] | None = None) -> int:
    """Creates the add/change/delete/view permissions of every model (link tables excluded);
    existing ones are kept. Returns how many were created."""
    if models is None:
        from ferro.models.base import registered_model_classes

        models = registered_model_classes()
    wanted = []
    for model in models:
        if getattr(model, "_auto_created", False) or "_meta" not in model.__dict__:
            continue
        for action in DEFAULT_ACTIONS:
            wanted.append(
                Permission(
                    codename=f"{action}_{model._meta.table}",
                    name=f"Can {action} {model.__name__}",
                )
            )
    before = await Permission.objects.count()
    if wanted:
        await Permission.objects.bulk_create(wanted, ignore_conflicts=True)
    return await Permission.objects.count() - before


def _request_from(handler: Callable[..., Any], decorator: str) -> None:
    if not inspect.iscoroutinefunction(handler):
        raise ConfigurationError(f"@{decorator} supports async def handlers only")
    if "request" not in inspect.signature(handler).parameters:
        raise ConfigurationError(f"@{decorator} needs the handler to declare a `request` parameter")


def _guard(
    decorator: str, check: Callable[[Any], Any]
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def wrap(handler: Callable[..., Any]) -> Callable[..., Any]:
        _request_from(handler, decorator)

        @functools.wraps(handler)
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            await check(kwargs["request"])
            return await handler(*args, **kwargs)

        # The OpenAPI document lists the session cookie as this route's security (Feature 026).
        guarded.__ferro_security__ = ("session",)  # type: ignore[attr-defined]
        return guarded

    return wrap


async def _authenticated(request: Any) -> Any:
    user = request.user
    if not user.is_authenticated:
        raise HTTPException(401, "Authentication required")
    return user


def login_required(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Anonymous requests get 401."""

    async def check(request: Any) -> None:
        await _authenticated(request)

    return _guard("login_required", check)(handler)


def staff_required(handler: Callable[..., Any]) -> Callable[..., Any]:
    """Anonymous requests get 401, non-staff users 403."""

    async def check(request: Any) -> None:
        user = await _authenticated(request)
        if not (user.is_active and user.is_staff):
            raise HTTPException(403, "Staff access required")

    return _guard("staff_required", check)(handler)


def permission_required(*codenames: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Anonymous requests get 401; users without every listed permission get 403."""
    if not codenames:
        raise ConfigurationError("permission_required() needs at least one permission")

    async def check(request: Any) -> None:
        user = await _authenticated(request)
        if not await user.has_perms(list(codenames)):
            raise HTTPException(403, "Permission denied")

    return _guard("permission_required", check)
