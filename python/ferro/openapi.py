"""OpenAPI 3.1 documents and Swagger UI (Feature 025): ``app.add_openapi(title=..., version=...)``.

The document is built from what the handlers declare: path, query, header and cookie
parameters with their types and defaults, Pydantic bodies, form fields and uploads, return
annotations with Pydantic models, and the 422 error format of :class:`ValidationError`.
"""

from __future__ import annotations

import html
import inspect
import json
import re
import sys
import typing
from typing import Any

from ferro import _ferro

__all__ = ["docs_page", "openapi_document"]

_REF = "#/components/schemas/{model}"
_TAIL = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*):path\}")
SWAGGER_UI = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14"


def _scalar_schema(converter: Any) -> dict[str, Any]:
    from ferro import routing

    return {
        routing._to_int: {"type": "integer"},
        routing._to_float: {"type": "number"},
        routing._to_bool: {"type": "boolean"},
    }.get(converter, {"type": "string"})


def _model_schema(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    """Moves Pydantic's ``$defs`` into ``components.schemas``."""
    for name, definition in schema.pop("$defs", {}).items():
        components.setdefault(name, definition)
    return schema


def _body_model(model: type, components: dict[str, Any]) -> dict[str, Any]:
    schema = _model_schema(model.model_json_schema(ref_template=_REF), components)
    components.setdefault(model.__name__, schema)
    return {"$ref": _REF.format(model=model.__name__)}


def _return_schema(handler: Any, components: dict[str, Any]) -> dict[str, Any] | None:
    pydantic = sys.modules.get("pydantic")
    if pydantic is None:
        return None
    try:
        annotation = inspect.signature(handler, eval_str=True).return_annotation
    except (NameError, TypeError, ValueError):
        return None
    if annotation is inspect.Signature.empty:
        return None
    from ferro.response import Response
    from ferro.routing import _mentions_model

    if not _mentions_model(annotation, pydantic.BaseModel):
        return None
    origin = typing.get_origin(annotation)
    if origin is typing.Union or str(origin) == "<class 'types.UnionType'>":
        kept = [
            arg
            for arg in typing.get_args(annotation)
            if not (isinstance(arg, type) and issubclass(arg, Response))
        ]
        annotation = typing.Union[tuple(kept)]  # noqa: UP007
    if isinstance(annotation, type) and issubclass(annotation, pydantic.BaseModel):
        return _body_model(annotation, components)  # a named component, like bodies
    try:
        schema = pydantic.TypeAdapter(annotation).json_schema(ref_template=_REF)
    except Exception:
        return None
    return _model_schema(schema, components)


_ERROR = {
    "type": "object",
    "properties": {
        "error": {
            "type": "object",
            "properties": {
                "status": {"type": "integer"},
                "message": {"type": "string"},
                "request_id": {"type": "string"},
                "details": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "param": {"type": "string"},
                            "location": {"type": "string"},
                            "problem": {"type": "string"},
                        },
                    },
                },
            },
        }
    },
}


def _flatten(specs: list[Any]) -> list[Any]:
    """The handler's parameters plus those its dependencies read from the request."""
    out = []
    for spec in specs:
        if spec.source == "depends":
            out.extend(_flatten(spec.dependency.specs))
        else:
            out.append(spec)
    return out


def _operation(record: Any, components: dict[str, Any]) -> dict[str, Any]:
    from ferro.routing import analyze

    names = _ferro.parse_path(record.path)
    specs = _flatten(analyze(record.handler, names, record.name))
    doc = inspect.getdoc(record.handler) or ""
    summary, _, description = doc.partition("\n")
    short = record.name.rsplit(".", 1)[-1].replace("_", " ").strip()
    operation: dict[str, Any] = {
        "operationId": re.sub(r"[^A-Za-z0-9_]", "_", record.name),
        "summary": summary.strip() or short.capitalize(),
    }
    if description.strip():
        operation["description"] = description.strip()
    if record.tags:
        operation["tags"] = list(record.tags)
    parameters = []
    seen = set()
    for spec in specs:
        if spec.source not in ("path", "query", "header", "cookie"):
            continue
        key = (spec.key, spec.source)
        if key in seen:
            continue
        seen.add(key)
        schema = _scalar_schema(spec.converter)
        if not spec.required and spec.default is not None:
            schema = {**schema, "default": spec.default}
        parameters.append(
            {
                "name": spec.key,
                "in": spec.source,
                "required": spec.source == "path" or spec.required,
                "schema": schema,
            }
        )
    if parameters:
        operation["parameters"] = parameters
    body = next((spec for spec in specs if spec.source == "body"), None)
    form = [spec for spec in specs if spec.source in ("form", "file")]
    content: dict[str, Any] = {}
    if body is not None:
        reference = _body_model(body.model, components)
        content["application/json"] = {"schema": reference}
        if not form:
            content["application/x-www-form-urlencoded"] = {"schema": reference}
    if form:
        properties: dict[str, Any] = {}
        required = []
        for spec in form:
            if spec.source == "file":
                one = {"type": "string", "format": "binary"}
                properties[spec.key] = {"type": "array", "items": one} if spec.many else one
            else:
                properties[spec.key] = _scalar_schema(spec.converter)
            if spec.required:
                required.append(spec.key)
        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        if body is not None:
            schema = {"allOf": [_body_model(body.model, components), schema]}
        content["multipart/form-data"] = {"schema": schema}
    if content:
        operation["requestBody"] = {
            "required": bool((body is not None and body.required) or any(s.required for s in form)),
            "content": content,
        }
    success: dict[str, Any] = {"description": "Successful response"}
    returned = _return_schema(record.handler, components)
    if returned is not None:
        success["content"] = {"application/json": {"schema": returned}}
    responses: dict[str, Any] = {"200": success}
    if any(spec.source != "request" for spec in specs):
        responses["422"] = {
            "description": "Validation error",
            "content": {"application/json": {"schema": {"$ref": _REF.format(model="Error")}}},
        }
        components.setdefault("Error", _ERROR)
    operation["responses"] = responses
    return operation


def openapi_document(
    app: Any, *, title: str, version: str, description: str | None = None
) -> dict[str, Any]:
    """The OpenAPI 3.1 document of ``app``'s HTTP routes (without WebSockets, the admin and
    routes registered with ``include_in_schema=False``)."""
    components: dict[str, Any] = {}
    paths: dict[str, Any] = {}
    for record in app.route_records:
        if record.websocket or not record.include_in_schema or record.name.startswith("admin:"):
            continue
        operation = _operation(record, components)
        path = _TAIL.sub(r"{\1}", record.path)
        entry = paths.setdefault(path, {})
        for method in record.methods:
            current = dict(operation)
            if len(record.methods) > 1:
                current["operationId"] = f"{operation['operationId']}_{method.lower()}"
            entry[method.lower()] = current
    document: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": title, "version": version},
        "paths": paths,
    }
    if description:
        document["info"]["description"] = description
    if components:
        document["components"] = {"schemas": components}
    return document


def docs_page(title: str, openapi_path: str) -> str:
    """A Swagger UI page for the document at ``openapi_path`` (assets from jsDelivr)."""
    url = json.dumps(openapi_path)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} - API docs</title>
<link rel="stylesheet" href="{SWAGGER_UI}/swagger-ui.css">
</head>
<body>
<div id="swagger-ui"></div>
<script src="{SWAGGER_UI}/swagger-ui-bundle.js"></script>
<script>
window.ui = SwaggerUIBundle({{url: {url}, dom_id: "#swagger-ui", deepLinking: true}});
</script>
</body>
</html>
"""
