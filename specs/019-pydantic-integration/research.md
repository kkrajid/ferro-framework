# Research: Pydantic Integration

## R1 — Detecting Pydantic without importing it

**Decision**: At route registration, an annotation is a Pydantic model when `pydantic` is already
in `sys.modules` and the annotation is a subclass of `pydantic.BaseModel` (also inside
`X | None`). A handler annotated with a Pydantic model has necessarily imported Pydantic.

**Rationale**: Ferro never imports Pydantic (constitution X: optional), costs nothing for apps
that do not use it, and needs no Ferro-specific marker (`Body(...)`) for the common case.

## R2 — Validating bodies

**Decision**: JSON bodies (no content type, `application/json`, `*+json`) go straight to
`Model.model_validate_json(bytes)` — Pydantic's own Rust JSON parser, one pass, no intermediate
`dict`. Form bodies (`application/x-www-form-urlencoded`) are turned into a dict (fields typed
as `list`/`set`/`tuple`/`frozenset` take every value, others the last one) and validated with
`model_validate`. Pydantic's `json_invalid` error becomes 400 (as `request.json()`); other
content types 415; an empty body 422 "missing request body" unless the parameter has a default.
Errors map to Ferro's existing detail format: `param` = the dotted error location (the
parameter name for errors on the whole body), `location` = `"body"`, `problem` = Pydantic's
message. Path/query problems are collected first and reported in the same 422.

**Alternatives**: decoding with Ferro's JSON decoder then `model_validate(dict)` — two passes and
loses Pydantic's strict JSON-mode types; a `Body()` marker — unnecessary ceremony.

## R3 — Encoding models

**Decision**: Ferro's Rust JSON encoder, on a value it does not know, checks for a Pydantic model
(`__pydantic_serializer__` and `model_dump_json`) and writes the result of `model_dump_json()`
into its output unchanged. This covers handler return values, `Response`/`JSONResponse` content,
nested values, `ws.send_json`, `broadcast.publish`, the cache and `json_dumps`.

**Rationale**: Zero cost for known types (the check is on the error path only); Pydantic's
serializer (Rust) produces the JSON directly; its JSON mode matches Ferro's conventions
(Decimal and UUID as strings, ISO 8601 datetimes with `Z`).

## R4 — Response models from return annotations

**Decision**: If a handler's return annotation mentions a Pydantic model, a `TypeAdapter` for it
is built at registration (Ferro `Response` classes removed from unions). Each result that is not
a `Response` and not `None` is validated with `from_attributes=True` (so Ferro model instances
and other objects are read by attribute) and sent as `Response(adapter.dump_json(value),
media_type="application/json")`. A validation failure raises an error naming the handler and the
annotation (500, logged). Accessing an unloaded relation raises `RelatedNotLoaded` (not an
`AttributeError`), which Pydantic lets through, so the log says which relation to load.

**Rationale**: FastAPI's well-known `response_model` behaviour expressed with the standard return
annotation; filtering by declared fields prevents leaking columns such as password hashes.

## R5 — Packaging

**Decision**: Optional extra `pydantic = ["pydantic>=2.7,<3"]`; Pydantic added to the dev extra
and CI so the integration is tested on every platform.
