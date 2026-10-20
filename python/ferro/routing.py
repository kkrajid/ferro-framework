"""Builds the endpoint the native server calls for each route: ``endpoint(request) -> coroutine``.

Handler parameters are filled by name (FR-007): ``request`` (or any parameter annotated
``Request``) receives the request, names matching a path parameter receive the path value, and
every other parameter receives the query parameter of the same name. ``int``, ``float``, ``bool``
and ``str`` annotations (optionally ``X | None``) are converted; failures become one
``ValidationError`` (422) listing every problem (FR-008).

With Pydantic installed (Feature 019), a parameter annotated with a Pydantic model receives the
validated request body, and a return annotation that uses Pydantic models converts what the
handler returns. Ferro never imports Pydantic: a handler annotated with a model already has.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import sys
import types
import typing
from collections.abc import Awaitable, Callable, Coroutine
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Any, Literal

from ferro._ferro import Request
from ferro.exceptions import ConfigurationError, HTTPException, ValidationError

__all__ = ["ParamSpec", "analyze", "build_endpoint", "build_websocket_endpoint"]

Endpoint = Callable[[Any], Coroutine[Any, Any, Any]]
Source = Literal["request", "path", "query", "body"]
Problems = list[dict[str, str]]

_FORM = "application/x-www-form-urlencoded"
_SEQUENCES = (list, set, frozenset, tuple)

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})


class _Problem(ValueError):
    pass


def _to_int(raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise _Problem(f"expected int, got {raw!r}") from None


def _to_float(raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        raise _Problem(f"expected float, got {raw!r}") from None


def _to_bool(raw: str) -> bool:
    text = raw.strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise _Problem(f"expected bool (true/false, 1/0, yes/no, on/off), got {raw!r}")


_CONVERTERS: dict[Any, Callable[[str], Any] | None] = {
    str: None,
    int: _to_int,
    float: _to_float,
    bool: _to_bool,
}


@dataclass(frozen=True, slots=True)
class ParamSpec:
    name: str
    source: Source
    converter: Callable[[str], Any] | None
    required: bool
    default: Any
    #: The Pydantic model of a body parameter.
    model: Any = None


def _pydantic_model(annotation: Any) -> type | None:
    """The Pydantic model an annotation names (``Model`` or ``Model | None``), else ``None``."""
    pydantic = sys.modules.get("pydantic")
    if pydantic is None:
        return None
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        inner = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(inner) != 1:
            return None
        annotation = inner[0]
    if isinstance(annotation, type) and issubclass(annotation, pydantic.BaseModel):
        return annotation
    return None


def _mentions_model(annotation: Any, base: type) -> bool:
    if isinstance(annotation, type) and issubclass(annotation, base):
        return True
    return any(_mentions_model(arg, base) for arg in typing.get_args(annotation))


def _unsupported(handler_name: str, name: str, annotation: Any) -> ConfigurationError:
    return ConfigurationError(
        f"handler {handler_name}: parameter '{name}' has unsupported annotation "
        f"{inspect.formatannotation(annotation)}; use str, int, float, bool, "
        f"an optional form of those (int | None), or Request"
    )


def _converter_for(annotation: Any, handler_name: str, name: str) -> Callable[[str], Any] | None:
    if annotation is inspect.Parameter.empty:
        return None
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        args = typing.get_args(annotation)
        inner = [a for a in args if a is not type(None)]
        if len(inner) == 1 and len(args) == 2 and inner[0] in _CONVERTERS:
            return _CONVERTERS[inner[0]]
        raise _unsupported(handler_name, name, annotation)
    if annotation in _CONVERTERS:
        return _CONVERTERS[annotation]
    raise _unsupported(handler_name, name, annotation)


def _is_connection(name: str, annotation: Any, socket: bool) -> bool:
    """Whether a parameter receives the request (HTTP) or the connection (WebSocket)."""
    if not socket:
        empty = annotation is inspect.Parameter.empty
        return annotation is Request or (name == "request" and empty)
    from ferro.websockets import WebSocket

    return annotation is WebSocket or (
        name in ("ws", "websocket") and annotation is inspect.Parameter.empty
    )


def analyze(
    handler: Callable[..., Any],
    path_params: list[str],
    handler_name: str,
    *,
    socket: bool = False,
) -> list[ParamSpec]:
    """Validates a handler signature at registration time (FR-005)."""
    try:
        signature = inspect.signature(handler, eval_str=True)
    except NameError as exc:
        raise ConfigurationError(
            f"handler {handler_name}: cannot resolve a type annotation: {exc}"
        ) from None
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"handler {handler_name}: cannot inspect signature: {exc}"
        ) from None

    specs: list[ParamSpec] = []
    for parameter in signature.parameters.values():
        name = parameter.name
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            raise ConfigurationError(
                f"handler {handler_name}: parameter '*{name}' is not supported"
            )
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            raise ConfigurationError(
                f"handler {handler_name}: parameter '**{name}' is not supported"
            )
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            raise ConfigurationError(
                f"handler {handler_name}: parameter '{name}' is positional-only; "
                f"handler parameters are passed by name"
            )
        annotation = parameter.annotation
        if _is_connection(name, annotation, socket):
            specs.append(ParamSpec(name, "request", None, True, None))
            continue
        model = _pydantic_model(annotation)
        if model is not None:
            specs.append(_body_spec(handler_name, parameter, model, specs, path_params, socket))
            continue
        converter = _converter_for(annotation, handler_name, name)
        if name in path_params:
            specs.append(ParamSpec(name, "path", converter, True, None))
        else:
            has_default = parameter.default is not inspect.Parameter.empty
            specs.append(
                ParamSpec(
                    name,
                    "query",
                    converter,
                    not has_default,
                    parameter.default if has_default else None,
                )
            )
    return specs


def _body_spec(
    handler_name: str,
    parameter: inspect.Parameter,
    model: type,
    specs: list[ParamSpec],
    path_params: list[str],
    socket: bool,
) -> ParamSpec:
    name = parameter.name
    if socket:
        raise ConfigurationError(
            f"WebSocket handler {handler_name}: parameter '{name}' is a Pydantic model, but "
            f"WebSocket handlers have no request body; validate messages with "
            f"{model.__name__}.model_validate_json(await ws.receive())"
        )
    if name in path_params:
        raise ConfigurationError(
            f"handler {handler_name}: path parameter '{name}' cannot be a Pydantic model"
        )
    other = next((spec.name for spec in specs if spec.source == "body"), None)
    if other is not None:
        raise ConfigurationError(
            f"handler {handler_name}: only one body parameter is allowed, got '{other}' and "
            f"'{name}' (combine them into one model)"
        )
    has_default = parameter.default is not inspect.Parameter.empty
    default = parameter.default if has_default else None
    return ParamSpec(name, "body", None, not has_default, default, model)


def _is_sequence(annotation: Any) -> bool:
    origin = typing.get_origin(annotation) or annotation
    if origin is typing.Union or origin is types.UnionType:
        return any(_is_sequence(arg) for arg in typing.get_args(annotation))
    return isinstance(origin, type) and issubclass(origin, _SEQUENCES)


def _body_reader(
    spec: ParamSpec,
) -> Callable[[Any], Awaitable[tuple[Any, Problems]]]:
    """``read(request) -> (model instance, problems)`` for a Pydantic body parameter. JSON is
    validated from the bytes by Pydantic's parser; form fields typed as sequences take every
    value of their key, others the last one."""
    model = spec.model
    invalid = sys.modules["pydantic"].ValidationError
    sequences = frozenset(
        field.alias or name
        for name, field in model.model_fields.items()
        if _is_sequence(field.annotation)
    )

    def problems_of(error: Any) -> Problems:
        details = error.errors(include_url=False, include_context=False, include_input=False)
        if any(detail["type"] == "json_invalid" for detail in details):
            raise HTTPException(400, f"Malformed JSON body: {details[0]['msg']}") from None
        return [
            {
                "param": ".".join(str(part) for part in detail["loc"]) or spec.name,
                "location": "body",
                "problem": detail["msg"],
            }
            for detail in details
        ]

    async def read(request: Any) -> tuple[Any, Problems]:
        body = await request.body()
        if not body:
            if spec.required:
                missing = {
                    "param": spec.name,
                    "location": "body",
                    "problem": "missing request body",
                }
                return None, [missing]
            return spec.default, []
        media = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        try:
            if media == _FORM:
                form = await request.form()
                data = {
                    key: form.getlist(key) if key in sequences else form.get(key) for key in form
                }
                return model.model_validate(data), []
            if media and media != "application/json" and not media.endswith("+json"):
                raise HTTPException(
                    415, f"Expected a JSON or form body for '{spec.name}', got {media}"
                )
            return model.model_validate_json(body), []
        except invalid as error:
            return None, problems_of(error)

    return read


def _extractor(specs: list[ParamSpec], *, collect: bool = False) -> Callable[[Any], Any]:
    """``extract(request) -> kwargs`` (raising one ``ValidationError``), or with ``collect``
    ``(kwargs, problems)``. Body parameters are read separately (:func:`_body_reader`)."""

    def extract(request: Any) -> Any:
        kwargs: dict[str, Any] = {}
        problems: Problems = []
        path_values = None
        query = None
        for spec in specs:
            if spec.source == "request":
                kwargs[spec.name] = request
                continue
            if spec.source == "body":
                continue
            if spec.source == "path":
                if path_values is None:
                    path_values = request.path_params
                raw = path_values.get(spec.name)
            else:
                if query is None:
                    query = request.query
                raw = query.get(spec.name)
            if raw is None:
                if spec.required:
                    problems.append(
                        {
                            "param": spec.name,
                            "location": spec.source,
                            "problem": f"missing required {spec.source} parameter",
                        }
                    )
                else:
                    kwargs[spec.name] = spec.default
                continue
            if spec.converter is None:
                kwargs[spec.name] = raw
                continue
            try:
                kwargs[spec.name] = spec.converter(raw)
            except _Problem as problem:
                problems.append(
                    {"param": spec.name, "location": spec.source, "problem": str(problem)}
                )
        if collect:
            return kwargs, problems
        if problems:
            raise ValidationError(problems)
        return kwargs

    return extract


def _sync_endpoint(
    handler: Callable[..., Any],
    specs: list[ParamSpec],
    get_executor: Callable[[], Executor],
) -> Endpoint:
    """Plain ``def`` handlers run on the bounded sync executor, never on the event loop (FR-006)."""
    extract = _extractor(specs) if specs else None

    async def endpoint(request: Any) -> Any:
        kwargs = extract(request) if extract is not None else {}
        loop = asyncio.get_running_loop()
        # A copy of this task's context, so the handler's thread sees its request id.
        context = contextvars.copy_context()
        call = functools.partial(handler, **kwargs)
        return await loop.run_in_executor(get_executor(), context.run, call)

    return endpoint


def _close_reason(error: ValidationError) -> str:
    text = "; ".join(f"{d['param']}: {d['problem']}" for d in error.details)
    encoded = text.encode("utf-8")[:123]
    return encoded.decode("utf-8", errors="ignore")


def build_websocket_endpoint(
    handler: Callable[..., Any],
    path_params: list[str],
    name: str,
    load_user: Callable[[Any], Coroutine[Any, Any, None]] | None = None,
) -> Endpoint:
    """``endpoint(native_socket)`` for ``@app.websocket`` handlers (Feature 018). Invalid
    parameters close the connection with 1008 and the problems as the reason."""
    if not inspect.iscoroutinefunction(handler):
        raise ConfigurationError(f"WebSocket handler {name} must be an async def function")
    specs = analyze(handler, path_params, name, socket=True)
    extract = _extractor(specs)

    async def endpoint(native: Any) -> None:
        from ferro.websockets import WebSocket

        ws = WebSocket(native)
        if load_user is not None:
            await load_user(ws)
        try:
            kwargs = extract(ws)
        except ValidationError as error:
            await ws.close(1008, _close_reason(error))
            return
        await handler(**kwargs)

    return endpoint


def _body_endpoint(
    handler: Callable[..., Any],
    specs: list[ParamSpec],
    body: ParamSpec,
    get_executor: Callable[[], Executor],
) -> Endpoint:
    """Path/query parameters and the Pydantic body, every problem in one 422 (Feature 019)."""
    collect = _extractor(specs, collect=True)
    read_body = _body_reader(body)
    is_async = inspect.iscoroutinefunction(handler)

    async def endpoint(request: Any) -> Any:
        kwargs, problems = collect(request)
        value, body_problems = await read_body(request)
        if problems or body_problems:
            raise ValidationError([*problems, *body_problems])
        kwargs[body.name] = value
        if is_async:
            return await handler(**kwargs)
        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()
        call = functools.partial(handler, **kwargs)
        return await loop.run_in_executor(get_executor(), context.run, call)

    return endpoint


def _response_adapter(handler: Callable[..., Any], name: str) -> Callable[[Any], Any] | None:
    """For a return annotation that uses Pydantic models (``-> ProductOut``,
    ``-> list[ProductOut]``): validates each result (reading objects such as Ferro model
    instances by attribute) and sends its JSON. ``Response`` objects and ``None`` pass through."""
    pydantic = sys.modules.get("pydantic")
    if pydantic is None:
        return None
    annotation = inspect.signature(handler, eval_str=True).return_annotation
    if annotation is inspect.Signature.empty or not _mentions_model(annotation, pydantic.BaseModel):
        return None
    from ferro.response import Response

    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        kept = [
            arg
            for arg in typing.get_args(annotation)
            if not (isinstance(arg, type) and issubclass(arg, Response))
        ]
        annotation = typing.Union[tuple(kept)]  # noqa: UP007 - built from a list
    shown = inspect.formatannotation(annotation)
    try:
        adapter = pydantic.TypeAdapter(annotation)
    except Exception as exc:
        raise ConfigurationError(
            f"handler {name}: return annotation {shown} cannot be used as a response model: {exc}"
        ) from None
    invalid = pydantic.ValidationError

    def respond(result: Any) -> Any:
        if result is None or isinstance(result, Response):
            return result
        try:
            value = adapter.validate_python(result, from_attributes=True)
        except invalid as exc:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in detail['loc']) or 'value'}: {detail['msg']}"
                for detail in exc.errors(include_url=False, include_input=False)
            )
            raise TypeError(
                f"handler {name} returned a value that does not match its return annotation "
                f"{shown}: {problems}{_relation_hints(exc)}"
            ) from None
        return Response(adapter.dump_json(value), media_type="application/json")

    return respond


def _relation_hints(error: Any) -> str:
    """How to load the related managers a response model could not read (Pydantic reports them
    only as "not a valid list")."""
    from ferro.models.exceptions import RelatedNotLoaded
    from ferro.models.relations import _Delegating

    hints: list[str] = []
    for detail in error.errors(include_url=False):
        manager = detail.get("input")
        if isinstance(manager, _Delegating):
            try:
                iter(manager)
            except RelatedNotLoaded as missing:
                hints.append(str(missing))
    return "".join(f"\n{hint}" for hint in dict.fromkeys(hints))


def build_endpoint(
    handler: Callable[..., Any],
    path_params: list[str],
    name: str,
    get_executor: Callable[[], Executor],
) -> Endpoint:
    endpoint = _call_endpoint(handler, path_params, name, get_executor)
    respond = _response_adapter(handler, name)
    if respond is None:
        return endpoint

    async def converted(request: Any) -> Any:
        return respond(await endpoint(request))

    return converted


def _call_endpoint(
    handler: Callable[..., Any],
    path_params: list[str],
    name: str,
    get_executor: Callable[[], Executor],
) -> Endpoint:
    specs = analyze(handler, path_params, name)
    body = next((spec for spec in specs if spec.source == "body"), None)
    if body is not None:
        return _body_endpoint(handler, specs, body, get_executor)
    if not inspect.iscoroutinefunction(handler):
        return _sync_endpoint(handler, specs, get_executor)

    # Fast paths: no per-request Python work beyond calling the handler.
    if not specs:
        return lambda request: handler()
    if len(specs) == 1 and specs[0].source == "request":
        request_name = specs[0].name
        return lambda request: handler(**{request_name: request})

    extract = _extractor(specs)

    async def endpoint(request: Any) -> Any:
        return await handler(**extract(request))

    return endpoint
