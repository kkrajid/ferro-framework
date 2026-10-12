"""Admin forms: widgets per field kind, parsing of submitted values and validation."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from ferro.admin.html import Markup, join, tag
from ferro.models.fields import Field

__all__ = ["FormField", "form_fields", "initial_values", "load_choices", "parse", "render"]

MAX_CHOICES = 500
REQUIRED = "This field is required."


@dataclass
class FormField:
    name: str
    label: str
    kind: str
    field: Any
    readonly: bool = False

    @property
    def relation(self) -> bool:
        return self.kind in ("ForeignKey", "OneToOne", "ManyToMany")

    @property
    def many(self) -> bool:
        return self.kind == "ManyToMany"

    @property
    def optional(self) -> bool:
        return self.many or getattr(self.field, "null", False)


def label_for(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def form_fields(model: type, readonly: list[str], adding: bool) -> list[FormField]:
    pk_name = model._meta.pk_name
    fields = []
    for name, field in model._fields.items():
        kind = field.kind
        if name == "password" and callable(getattr(model, "set_password", None)):
            # Never shown; a value typed in is hashed with `set_password` (Feature 017).
            kind = "Password"
        fixed = name in readonly or (name == pk_name and not adding)
        fields.append(FormField(name, label_for(name), kind, field, fixed))
    for name, relation in model._many_to_many.items():
        # Links through a custom model carry data of their own: edit them on that model's page.
        fixed = name in readonly or not relation.auto_created
        fields.append(FormField(name, label_for(name), "ManyToMany", relation, fixed))
    return fields


def key_kind(model: type) -> str:
    """Field kind of a model's primary key (BigInteger for the automatic one)."""
    pk_name = model._meta.pk_name
    field = model._fields.get(pk_name)
    return field.kind if field is not None else "BigInteger"


def convert_key(model: type, raw: str) -> Any:
    """A primary key from a URL or form value; ``ValueError`` when it cannot be one."""
    return convert(key_kind(model), raw.strip(), None)


def convert(kind: str, raw: str, field: Field | None) -> Any:
    """Converts a submitted string; raises ``ValueError`` with the message to show."""
    if kind == "String":
        limit = getattr(field, "max_length", None)
        if limit is not None and len(raw) > limit:
            raise ValueError(
                f"Ensure this value has at most {limit} characters (it has {len(raw)})."
            )
        return raw
    if kind == "Text":
        return raw
    if kind in ("Integer", "BigInteger"):
        try:
            return int(raw)
        except ValueError:
            raise ValueError("Enter a whole number.") from None
    if kind == "Float":
        try:
            value = float(raw)
        except ValueError:
            raise ValueError("Enter a number.") from None
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("Enter a number.")
        return value
    if kind == "Decimal":
        try:
            value = Decimal(raw)
        except InvalidOperation:
            raise ValueError("Enter a number.") from None
        if not value.is_finite():
            raise ValueError("Enter a number.")
        return value
    if kind == "Date":
        try:
            return date.fromisoformat(raw)
        except ValueError:
            raise ValueError("Enter a valid date.") from None
    if kind == "Time":
        try:
            return time.fromisoformat(raw)
        except ValueError:
            raise ValueError("Enter a valid time.") from None
    if kind == "DateTime":
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            raise ValueError("Enter a valid date and time.") from None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if kind == "UUID":
        try:
            return uuid.UUID(raw)
        except ValueError:
            raise ValueError("Enter a valid UUID.") from None
    if kind == "JSON":
        try:
            return json.loads(raw)
        except ValueError:
            raise ValueError("Enter valid JSON.") from None
    if kind == "Boolean":
        return raw.lower() in ("on", "true", "1", "yes")
    raise ValueError(f"Unsupported field kind {kind}.")


def to_text(kind: str, value: Any) -> str:
    """A stored value as the string a form input shows."""
    if value is None:
        return ""
    if kind == "DateTime":
        value = value.astimezone(UTC)
        return value.strftime("%Y-%m-%dT%H:%M" if value.second == 0 else "%Y-%m-%dT%H:%M:%S")
    if kind in ("Date", "Time"):
        return value.isoformat()
    if kind == "JSON":
        return json.dumps(value, ensure_ascii=False)
    return str(value)


async def load_choices(fields: list[FormField]) -> dict[str, list[tuple[str, str]] | None]:
    """Related rows for select lists (``None`` when there are too many: a key input is shown)."""
    choices: dict[str, list[tuple[str, str]] | None] = {}
    for field in fields:
        if not field.relation or field.readonly:
            continue
        target = field.field.target
        rows = await target.objects.order_by("pk")[: MAX_CHOICES + 1]
        if len(rows) > MAX_CHOICES:
            choices[field.name] = None
        else:
            choices[field.name] = [(to_text(key_kind(target), row.pk), str(row)) for row in rows]
    return choices


async def initial_values(obj: Any, fields: list[FormField]) -> dict[str, Any]:
    """Form strings (lists for many-to-many) from a saved instance."""
    values: dict[str, Any] = {}
    for field in fields:
        if field.many:
            keys = await getattr(obj, field.name).values_list("pk", flat=True)
            target_kind = key_kind(field.field.target)
            values[field.name] = [to_text(target_kind, key) for key in keys]
        elif field.relation:
            values[field.name] = to_text(
                key_kind(field.field.target), obj.__dict__.get(field.field.attname)
            )
        elif field.kind == "Boolean":
            values[field.name] = bool(obj.__dict__.get(field.name))
        elif field.kind == "Password":
            values[field.name] = ""
        else:
            values[field.name] = to_text(field.kind, obj.__dict__.get(field.name))
    return values


def defaults(model: type, fields: list[FormField]) -> dict[str, Any]:
    """Initial form strings for a new object (field defaults)."""
    values: dict[str, Any] = {}
    for field in fields:
        if field.many or not isinstance(field.field, Field) or not field.field.has_default():
            continue
        default = field.field.get_default()
        if field.kind == "Boolean":
            values[field.name] = bool(default)
        elif not field.relation:
            values[field.name] = to_text(field.kind, default)
    return values


def parse(
    form: Any, fields: list[FormField], adding: bool
) -> tuple[dict[str, Any], dict[str, list[Any]], dict[str, Any], dict[str, str]]:
    """``(values, many_to_many, entered, errors)`` from submitted form data. ``entered`` holds
    the raw strings to show again when there are errors."""
    values: dict[str, Any] = {}
    links: dict[str, list[Any]] = {}
    entered: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for field in fields:
        if field.readonly:
            continue
        if field.many:
            raw_list = form.getlist(field.name)
            entered[field.name] = raw_list
            target = field.field.target
            try:
                links[field.name] = [convert_key(target, raw) for raw in raw_list if raw]
            except ValueError:
                errors[field.name] = "Select valid choices."
            continue
        if field.kind == "Boolean":
            entered[field.name] = field.name in form
            values[field.name] = field.name in form
            continue
        if field.kind == "Password":
            raw = form.get(field.name, "")
            entered[field.name] = ""
            if raw:
                values[field.name] = raw  # hashed by the admin before saving
            continue
        raw = form.get(field.name, "")
        entered[field.name] = raw
        if field.kind not in ("String", "Text") or raw == "":
            raw = raw.strip()
        if raw == "":
            if field.optional:
                values[field.name] = None
            elif adding and field.field.has_default():
                continue
            else:
                errors[field.name] = REQUIRED
            continue
        try:
            if field.relation:
                values[field.name] = convert_key(field.field.target, raw)
            else:
                values[field.name] = convert(field.kind, raw, field.field)
        except ValueError as problem:
            errors[field.name] = str(problem)
    return values, links, entered, errors


def _widget(field: FormField, value: Any, choices: list[tuple[str, str]] | None) -> Markup:
    name = field.name
    if field.relation and choices is not None:
        selected = set(value) if field.many else {value}
        options = []
        if not field.many and field.optional:
            options.append(tag("option", "---------", value=""))
        for key, label in choices:
            options.append(tag("option", label, value=key, selected=key in selected))
        return tag("select", *options, name=name, multiple=field.many, id=name)
    if field.many:
        return tag("input", type="text", name=name, id=name, value=",".join(value or []))
    kind = field.kind
    if kind == "Boolean":
        return tag("input", type="checkbox", name=name, id=name, checked=bool(value))
    if kind in ("Text", "JSON"):
        return tag("textarea", value or "", name=name, id=name)
    if kind == "Password":
        return tag(
            "input", type="password", name=name, id=name, value="", autocomplete="new-password"
        )
    attributes: dict[str, Any] = {"type": "text", "name": name, "id": name, "value": value or ""}
    if kind == "String":
        attributes["maxlength"] = field.field.max_length
    elif kind in ("Integer", "BigInteger"):
        attributes.update(type="number", step="1")
    elif kind in ("Float", "Decimal"):
        attributes.update(type="number", step="any")
    elif kind == "Date":
        attributes["type"] = "date"
    elif kind == "Time":
        attributes.update(type="time", step="1")
    elif kind == "DateTime":
        attributes.update(type="datetime-local", step="1")
    return tag("input", **attributes)


def render(
    fields: list[FormField],
    values: dict[str, Any],
    errors: dict[str, str],
    choices: dict[str, list[tuple[str, str]] | None],
    readonly_values: dict[str, str],
    action: str,
    submit: str,
    extra: Any = None,
    general: list[str] | None = None,
) -> Markup:
    rows = []
    if general:
        rows.append(tag("ul", *(tag("li", problem) for problem in general), class_="errors"))
    for field in fields:
        label = tag("label", field.label, for_=field.name)
        if field.readonly:
            rows.append(
                join(label, tag("div", readonly_values.get(field.name, ""), class_="readonly"))
            )
            continue
        error = errors.get(field.name)
        rows.append(
            join(
                label,
                _widget(
                    field, values.get(field.name, [] if field.many else ""), choices.get(field.name)
                ),
                tag("div", error, class_="error") if error else None,
            )
        )
    rows.append(tag("div", tag("button", submit, type="submit"), extra, class_="actions"))
    return tag("form", *rows, method="post", action=action, class_="edit")
