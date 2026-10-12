"""The admin site: model registry and the pages mounted on an app (Feature 008)."""

from __future__ import annotations

import functools
import operator
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode

from ferro.admin.forms import (
    convert,
    convert_key,
    defaults,
    form_fields,
    initial_values,
    label_for,
    load_choices,
    parse,
    render,
    to_text,
)
from ferro.admin.html import HEADERS, Markup, join, page, tag
from ferro.admin.options import ModelAdmin
from ferro.auth import authenticate, login, logout
from ferro.auth.hashers import PasswordError
from ferro.db import transaction
from ferro.exceptions import ConfigurationError
from ferro.models import Count, FieldError, IntegrityError, ProtectedError, Q
from ferro.models.relations import CASCADE, ForeignKey
from ferro.response import Response

if TYPE_CHECKING:
    from ferro.app import App

# Fields of the user model only superusers may change.
_PRIVILEGED = ("is_superuser", "groups", "user_permissions")


def _is_user_model(cls: type) -> bool:
    from ferro.auth.models import User

    return cls is User


__all__ = ["AdminSite", "admin"]

ACTIONS = ("view", "add", "change", "delete")
MAX_FILTER_CHOICES = 100


def _redirect(location: str) -> Response:
    return Response(None, status=303, headers={**HEADERS, "location": location})


def _display(model: type, obj: Any, name: str) -> str:
    """A cell value as text."""
    if name == "__str__":
        return str(obj)
    field = model._fields.get(name)
    if isinstance(field, ForeignKey):
        related = obj._related.get(name) if "_related" in obj.__dict__ else None
        if related is not None:
            return str(related)
        key = obj.__dict__.get(field.attname)
        return "-" if key is None else str(key)
    value = obj.__dict__.get(name if name != "pk" else model._meta.pk_name)
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if field is not None:
        return to_text(field.kind, value)
    return str(value)


def _default_columns(model: type) -> list[str]:
    if "__str__" in model.__dict__:
        return ["__str__"]
    names = [model._meta.pk_name, *[n for n in model._fields if n != model._meta.pk_name]]
    return names[:4]


class AdminSite:
    """A set of registered models served under one prefix (``app.add_admin(site)``)."""

    def __init__(self, title: str = "Ferro administration") -> None:
        self.title = title
        self._registry: dict[type, ModelAdmin] = {}
        self.prefix = "/admin"

    def register(self, model: type, options: ModelAdmin | None = None) -> None:
        if "_meta" not in getattr(model, "__dict__", {}):
            raise ConfigurationError(f"{model!r} is not a registered model")
        if model in self._registry:
            raise ConfigurationError(f"{model.__name__} is already registered with this admin")
        if model._meta.table in ("login", "logout"):
            raise ConfigurationError(f"{model.__name__}: table '{model._meta.table}' is reserved")
        options = options or ModelAdmin()
        options.check(model)
        self._registry[model] = options

    def _lookup(self, table: str) -> tuple[type, ModelAdmin] | None:
        for model, options in self._registry.items():
            if model._meta.table == table:
                return model, options
        return None

    # -- access ---------------------------------------------------------------------------

    def _page(self, request: Any, title: str, body: Any, status: int = 200) -> Response:
        message = request.session.pop("_admin_message", None) if status == 200 else None
        return page(
            title, body, prefix=self.prefix, user=request.user, message=message, status=status
        )

    def _not_found(self, request: Any) -> Response:
        return self._page(request, "Not found", tag("p", "There is no such page."), status=404)

    def _forbidden(self, request: Any) -> Response:
        return self._page(
            request,
            "Permission denied",
            tag("p", "You do not have permission to see this page."),
            status=403,
        )

    async def _staff(self, request: Any) -> Response | None:
        user = request.user
        if not user.is_authenticated:
            target = request.path + (f"?{request.query_string}" if request.query_string else "")
            return _redirect(f"{self.prefix}/login?next={quote(target, safe='')}")
        if not (user.is_active and user.is_staff):
            return self._forbidden(request)
        return None

    async def _can(self, user: Any, model: type, *actions: str) -> bool:
        """Whether ``user`` holds any of ``actions`` on ``model``. The four model permissions
        are loaded in one query and cached on the user for the request."""
        table = model._meta.table
        await user.has_perms([f"{action}_{table}" for action in ACTIONS])
        for action in actions:
            if await user.has_perm(f"{action}_{table}"):
                return True
        return False

    def _safe_next(self, target: str | None) -> str:
        if (
            target
            and target.startswith(self.prefix)
            and not target.startswith("//")
            and "\\" not in target
        ):
            return target
        return self.prefix

    # -- pages ----------------------------------------------------------------------------

    async def index(self, request: Any) -> Response:
        denied = await self._staff(request)
        if denied:
            return denied
        rows = []
        for model in sorted(self._registry, key=lambda m: m.__name__):
            if not await self._can(request.user, model, "view", "change"):
                continue
            table = model._meta.table
            add = None
            if await self._can(request.user, model, "add"):
                add = tag("a", "Add", href=f"{self.prefix}/{table}/add")
            rows.append(
                tag(
                    "tr",
                    tag("td", tag("a", model.__name__, href=f"{self.prefix}/{table}")),
                    tag("td", add),
                )
            )
        body = tag("table", *rows) if rows else tag("p", "You cannot view any model.")
        return self._page(request, self.title, body)

    async def login_view(self, request: Any) -> Response:
        if request.method == "POST":
            form = await request.form()
            next_url = self._safe_next(form.get("next"))
            user = await authenticate(form.get("username", ""), form.get("password", ""))
            if user is not None and user.is_active and user.is_staff:
                await login(request, user)
                return _redirect(next_url)
            error = (
                "Please enter the correct username and password for a staff account. Both "
                "fields may be case-sensitive."
            )
            username = form.get("username", "")
        else:
            next_url = self._safe_next(request.query.get("next"))
            if request.user.is_authenticated and request.user.is_staff:
                return _redirect(next_url)
            error, username = None, ""
        body = tag(
            "form",
            tag("p", error, class_="errors") if error else None,
            tag("label", "Username", for_="username"),
            tag("input", type="text", name="username", id="username", value=username),
            tag("label", "Password", for_="password"),
            tag("input", type="password", name="password", id="password"),
            tag("input", type="hidden", name="next", value=next_url),
            tag("div", tag("button", "Log in", type="submit"), class_="actions"),
            method="post",
            action=f"{self.prefix}/login",
            class_="edit",
        )
        return self._page(request, "Log in", body)

    async def logout_view(self, request: Any) -> Response:
        await logout(request)
        return _redirect(f"{self.prefix}/login")

    async def list_view(self, request: Any, model: str) -> Response:
        denied = await self._staff(request)
        if denied:
            return denied
        found = self._lookup(model)
        if found is None:
            return self._not_found(request)
        cls, options = found
        user = request.user
        if not await self._can(user, cls, "view", "change"):
            return self._forbidden(request)
        can_change = await self._can(user, cls, "change")
        params = request.query
        queryset = cls.objects.all()
        search = (params.get("q") or "").strip()
        if search and options.search_fields:
            conditions = [Q(**{f"{name}__icontains": search}) for name in options.search_fields]
            queryset = queryset.filter(functools.reduce(operator.or_, conditions))
        for name in options.list_filter:
            raw = params.get(name)
            if raw in (None, ""):
                continue
            value = self._filter_value(cls, name, raw)
            if value is not None:
                queryset = queryset.filter(**{name: value})
        ordering = params.get("o") or ""
        sortable = set(cls._fields) | {"pk"}
        if ordering.lstrip("-") in sortable:
            queryset = queryset.order_by(ordering)
        else:
            queryset = queryset.order_by(*(options.ordering or ["-pk"]))
        total = await queryset.count()
        try:
            number = max(1, int(params.get("page") or 1))
        except ValueError:
            number = 1
        per_page = options.list_per_page
        columns = list(options.list_display) or _default_columns(cls)
        related = [n for n in columns if isinstance(cls._fields.get(n), ForeignKey)]
        if related:
            queryset = queryset.select_related(*related)
        rows = await queryset[(number - 1) * per_page : number * per_page]

        table = cls._meta.table
        header = tag(
            "tr", *(tag("th", cls.__name__ if c == "__str__" else label_for(c)) for c in columns)
        )
        body_rows = []
        for obj in rows:
            cells = []
            for index, column in enumerate(columns):
                text = _display(cls, obj, column)
                if index == 0 and can_change:
                    key = to_text_key(cls, obj.pk)
                    text = tag("a", text, href=f"{self.prefix}/{table}/{key}/change")
                cells.append(tag("td", text))
            body_rows.append(tag("tr", *cells))
        bar = [
            tag(
                "form",
                tag("input", type="search", name="q", value=search, placeholder="Search"),
                " ",
                tag("button", "Search", type="submit"),
                method="get",
                class_="inline",
            )
            if options.search_fields
            else None,
            tag("a", "Add", href=f"{self.prefix}/{table}/add")
            if await self._can(user, cls, "add")
            else None,
            tag("span", f"{total} row" if total == 1 else f"{total} rows", class_="total"),
        ]
        pages = []
        items = [(k, v) for k, v in params.items() if k != "page"]
        if number > 1:
            pages.append(
                tag("a", "Previous", href="?" + urlencode([*items, ("page", str(number - 1))]))
            )
        if number * per_page < total:
            pages.append(
                tag("a", "Next", href="?" + urlencode([*items, ("page", str(number + 1))]))
            )
        content = join(
            tag("div", *bar, class_="bar"),
            await self._filters(cls, options, params),
            tag("table", header, *body_rows),
            tag("div", *pages, class_="bar") if pages else None,
        )
        return self._page(request, cls.__name__, content)

    def _filter_value(self, cls: type, name: str, raw: str) -> Any:
        field = cls._fields[name]
        try:
            if isinstance(field, ForeignKey):
                return convert_key(field.target, raw)
            if field.kind == "Boolean":
                return raw.lower() in ("true", "1", "yes", "on")
            return convert(field.kind, raw, field)
        except ValueError:
            return None

    async def _filters(self, cls: type, options: ModelAdmin, params: Any) -> Markup | None:
        if not options.list_filter:
            return None
        groups = []
        for name in options.list_filter:
            field = cls._fields[name]
            if field.kind == "Boolean":
                choices = [("true", "Yes"), ("false", "No")]
            elif isinstance(field, ForeignKey):
                rows = await field.target.objects.order_by("pk")[:MAX_FILTER_CHOICES]
                choices = [(to_text_key(field.target, row.pk), str(row)) for row in rows]
            else:
                values = await (
                    cls.objects.values(name).annotate(n=Count()).order_by(name)[:MAX_FILTER_CHOICES]
                )
                choices = [
                    (to_text(field.kind, row[name]), to_text(field.kind, row[name]))
                    for row in values
                    if row[name] is not None
                ]
            keep = [(k, v) for k, v in params.items() if k not in (name, "page")]
            links = [tag("a", "All", href="?" + urlencode(keep) if keep else "?")]
            for value, label in choices:
                links.append(tag("a", label, href="?" + urlencode([*keep, (name, value)])))
            groups.append(tag("div", tag("strong", label_for(name)), " ", *links))
        return tag("div", *groups, class_="filters")

    async def _object(self, cls: type, pk: str) -> Any:
        try:
            key = convert_key(cls, pk)
        except ValueError:
            return None
        return await cls.objects.filter(pk=key).first()

    async def edit_view(self, request: Any, model: str, pk: str | None = None) -> Response:
        denied = await self._staff(request)
        if denied:
            return denied
        found = self._lookup(model)
        if found is None:
            return self._not_found(request)
        cls, options = found
        adding = pk is None
        if not await self._can(request.user, cls, "add" if adding else "change"):
            return self._forbidden(request)
        obj = None
        if not adding:
            obj = await self._object(cls, pk)
            if obj is None:
                return self._not_found(request)
        fixed = list(options.readonly_fields)
        if _is_user_model(cls) and not request.user.is_superuser:
            # Only superusers may grant superuser status or permissions (no self-escalation).
            fixed += [name for name in _PRIVILEGED if name not in fixed]
        fields = form_fields(cls, fixed, adding)
        readonly = {
            f.name: ("-" if obj is None or f.kind == "Password" else _display(cls, obj, f.name))
            for f in fields
            if f.readonly and not f.many
        }
        table = cls._meta.table
        action = (
            f"{self.prefix}/{table}/add"
            if adding
            else f"{self.prefix}/{table}/{to_text_key(cls, obj.pk)}/change"
        )
        errors: dict[str, str] = {}
        general: list[str] = []
        if request.method == "POST":
            form = await request.form()
            values, links, entered, errors = parse(form, fields, adding)
            if not errors:
                try:
                    saved = await self._save(cls, obj, values, links)
                except IntegrityError as error:
                    for name in error.fields:
                        if name in cls._fields:
                            errors[name] = (
                                f"{cls.__name__} with this {label_for(name).lower()} already "
                                "exists."
                            )
                    if not errors:
                        general.append(f"Could not save: {error}")
                except FieldError as error:
                    general.append(str(error))
                except PasswordError as error:
                    errors["password"] = error.message
                else:
                    verb = "added" if adding else "changed"
                    request.session["_admin_message"] = f'The {cls.__name__} "{saved}" was {verb}.'
                    return _redirect(f"{self.prefix}/{table}/{to_text_key(cls, saved.pk)}/change")
            values_to_show = entered
        else:
            values_to_show = defaults(cls, fields) if adding else await initial_values(obj, fields)
        extra = None
        if not adding and await self._can(request.user, cls, "delete"):
            extra = tag(
                "a", "Delete", href=f"{self.prefix}/{table}/{to_text_key(cls, obj.pk)}/delete"
            )
        body = render(
            fields,
            values_to_show,
            errors,
            await load_choices(fields),
            readonly,
            action,
            "Save",
            extra,
            general,
        )
        title = f"Add {cls.__name__}" if adding else f"Change {cls.__name__}"
        return self._page(request, title, body)

    async def _save(
        self, cls: type, obj: Any, values: dict[str, Any], links: dict[str, list]
    ) -> Any:
        password = values.pop("password", None) if _is_user_model(cls) else None
        async with transaction():
            if obj is None:
                fields = {}
                for name, value in values.items():
                    field = cls._fields.get(name)
                    fields[field.attname if isinstance(field, ForeignKey) else name] = value
                obj = cls(**fields)
            else:
                for name, value in values.items():
                    field = cls._fields.get(name)
                    setattr(obj, field.attname if isinstance(field, ForeignKey) else name, value)
            if password is not None:
                await obj.set_password(password)
            await obj.save()
            for name, keys in links.items():
                await getattr(obj, name).set(keys)
        return obj

    async def _relations(self, cls: type, obj: Any) -> tuple[list[str], list[str]]:
        """``(cascading, protecting)`` descriptions of rows referring to ``obj``."""
        from ferro.models.base import registered_model_classes

        cascading, protecting = [], []
        for source in registered_model_classes():
            if getattr(source, "_auto_created", False):
                continue
            for field in source._relation_fields.values():
                if field.target is not cls:
                    continue
                count = await source.objects.filter(**{field.name: obj.pk}).count()
                if not count:
                    continue
                text = f"{count} {source.__name__}"
                if field.on_delete == CASCADE:
                    cascading.append(text)
                elif field.on_delete != "set_null":
                    protecting.append(text)
        return cascading, protecting

    async def delete_view(self, request: Any, model: str, pk: str) -> Response:
        denied = await self._staff(request)
        if denied:
            return denied
        found = self._lookup(model)
        if found is None:
            return self._not_found(request)
        cls, _ = found
        if not await self._can(request.user, cls, "delete"):
            return self._forbidden(request)
        obj = await self._object(cls, pk)
        if obj is None:
            return self._not_found(request)
        table = cls._meta.table
        cascading, protecting = await self._relations(cls, obj)

        def refused(items: list[str]) -> Response:
            body = join(
                tag(
                    "p",
                    f'The {cls.__name__} "{obj}" cannot be deleted because other rows refer to it:',
                ),
                tag("ul", *(tag("li", item) for item in items)),
            )
            return self._page(request, f"Cannot delete {cls.__name__}", body)

        if protecting:
            return refused(protecting)
        if request.method == "POST":
            try:
                await obj.delete()
            except ProtectedError:
                return refused(["rows protected by the database"])
            request.session["_admin_message"] = f'The {cls.__name__} "{obj}" was deleted.'
            return _redirect(f"{self.prefix}/{table}")
        body = join(
            tag("p", f'Delete the {cls.__name__} "{obj}"?'),
            tag("p", "These related rows will be deleted too:") if cascading else None,
            tag("ul", *(tag("li", item) for item in cascading)) if cascading else None,
            tag(
                "form",
                tag("button", "Yes, delete", type="submit", class_="danger"),
                " ",
                tag("a", "Cancel", href=f"{self.prefix}/{table}/{to_text_key(cls, obj.pk)}/change"),
                method="post",
                class_="actions",
            ),
        )
        return self._page(request, f"Delete {cls.__name__}", body)

    # -- mounting -------------------------------------------------------------------------

    def mount(self, app: App, prefix: str = "/admin") -> None:
        """Adds the admin routes to ``app`` under ``prefix``."""
        prefix = "/" + prefix.strip("/")
        self.prefix = prefix
        site = self

        async def index(request):
            return await site.index(request)

        async def sign_in(request):
            return await site.login_view(request)

        async def sign_out(request):
            return await site.logout_view(request)

        async def changelist(request, model: str):
            return await site.list_view(request, model)

        async def add(request, model: str):
            return await site.edit_view(request, model)

        async def change(request, model: str, pk: str):
            return await site.edit_view(request, model, pk)

        async def delete(request, model: str, pk: str):
            return await site.delete_view(request, model, pk)

        app.route(prefix, ["GET"], name="admin:index")(index)
        app.route(f"{prefix}/login", ["GET", "POST"], name="admin:login", rate_limit="10/minute")(
            sign_in
        )
        app.route(f"{prefix}/logout", ["POST"], name="admin:logout")(sign_out)
        app.route(f"{prefix}/{{model}}", ["GET"], name="admin:list")(changelist)
        app.route(f"{prefix}/{{model}}/add", ["GET", "POST"], name="admin:add")(add)
        app.route(f"{prefix}/{{model}}/{{pk}}/change", ["GET", "POST"], name="admin:change")(change)
        app.route(f"{prefix}/{{model}}/{{pk}}/delete", ["GET", "POST"], name="admin:delete")(delete)


def to_text_key(cls: type, key: Any) -> str:
    """A primary key as it appears in URLs."""
    return quote(str(key), safe="")


admin = AdminSite()
"""The default admin site: ``from ferro.admin import admin; admin.register(Model)``."""
