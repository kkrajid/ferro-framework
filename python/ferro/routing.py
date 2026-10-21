"""Builds the endpoint the native server calls for each route: ``endpoint(request) -> coroutine``.

Handler parameters are filled by name (FR-007): ``request`` (or any parameter annotated
``Request``) receives the request, names matching a path parameter receive the path value, and
every other parameter receives the query parameter of the same name. ``int``, ``float``, ``bool``
and ``str`` annotations (optionally ``X | None``) are converted; failures become one
``ValidationError`` (422) listing every problem (FR-008).

With Pydantic installed (Feature 019), a parameter annotated with a Pydantic model receives the
validated request body, and a return annotation that uses Pydantic models converts what the
handler returns. Ferro never imports Pydantic: a handler annotated with a model already has.

Feature 025 adds ``UploadFile`` parameters (files of a multipart body), the ``Form``,
``Header`` and ``Cookie`` markers, ``Depends`` and ``BackgroundTasks``.
"""

from __future__ import annotations

import asyncio
import contextlib
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
Source = Literal[
    "request", "path", "query", "body", "form", "header", "cookie", "file", "depends", "background"
]
# Sources that need the asynchronous endpoint (body, form, dependencies, background tasks).
_RICH = frozenset({"body", "form", "file", "depends", "background"})
Problems = list[dict[str, str]]

_FORM = "application/x-www-form-urlencoded"
_MULTIPART = "multipart/form-data"
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
    #: Field, header or cookie name when it differs from the parameter name.
    alias: str | None = None
    #: ``list[UploadFile]``: every file of the field.
    many: bool = False
    #: The resolved dependency of a ``Depends`` parameter.
    dependency: Any = None

    @property
    def key(self) -> str:
        return self.alias or self.name


@dataclass(frozen=True, slots=True)
class _Dependency:
    call: Callable[..., Any]
    specs: list[ParamSpec]
    extract: Callable[[Any], Any]
    use_cache: bool
    kind: str  # "async", "sync", "async_generator", "generator"


def _upload_annotation(annotation: Any) -> tuple[bool, bool]:
    """``(is an upload, many)`` for ``UploadFile``, ``UploadFile | None`` and
    ``list[UploadFile]``."""
    from ferro.uploads import UploadFile

    if annotation is UploadFile:
        return True, False
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Union or origin is types.UnionType:
        inner = [a for a in args if a is not type(None)]
        if len(inner) == 1:
            return _upload_annotation(inner[0])
    if origin in (list, typing.List) and args == (UploadFile,):  # noqa: UP006
        return True, True
    return False, False


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
        special = _special_spec(handler_name, parameter, path_params, socket)
        if special is not None:
            specs.append(special)
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


def _special_spec(
    handler_name: str, parameter: inspect.Parameter, path_params: list[str], socket: bool
) -> ParamSpec | None:
    """Specs for ``Depends``/``Form``/``Header``/``Cookie`` defaults and for ``UploadFile`` and
    ``BackgroundTasks`` annotations (Feature 025); ``None`` for ordinary parameters."""
    from ferro.background import BackgroundTasks
    from ferro.params import Depends, _Marker

    name, annotation, default = parameter.name, parameter.annotation, parameter.default
    kind = "WebSocket handler" if socket else "handler"
    if isinstance(default, Depends):
        if socket:
            raise ConfigurationError(
                f"{kind} {handler_name}: Depends() is not supported on WebSocket handlers; "
                "call the function at the start of the handler"
            )
        return ParamSpec(
            name, "depends", None, True, None, dependency=_dependency(default, path_params)
        )
    if annotation is BackgroundTasks:
        if socket:
            raise ConfigurationError(
                f"{kind} {handler_name}: BackgroundTasks is not available on WebSockets; "
                "start a task with asyncio.create_task()"
            )
        return ParamSpec(name, "background", None, True, None)
    upload, many = _upload_annotation(annotation)
    if upload:
        if socket:
            raise ConfigurationError(f"{kind} {handler_name}: '{name}' cannot be an upload")
        optional = default is not inspect.Parameter.empty
        alias = default.alias if isinstance(default, _Marker) else None
        if isinstance(default, _Marker):
            optional = not default.required
        return ParamSpec(name, "file", None, not optional, None, alias=alias, many=many)
    if isinstance(default, _Marker):
        if default.source == "form" and socket:
            raise ConfigurationError(f"{kind} {handler_name}: WebSockets have no form body")
        if name in path_params:
            raise ConfigurationError(
                f"{kind} {handler_name}: path parameter '{name}' cannot use {default!r}"
            )
        alias = default.alias
        if alias is None and default.source == "header":
            alias = name.replace("_", "-")
        if default.source == "header":
            alias = alias.lower()
        return ParamSpec(
            name,
            default.source,  # type: ignore[arg-type]
            _converter_for(annotation, handler_name, name),
            default.required,
            None if default.required else default.default,
            alias=alias,
        )
    return None


def _dependency(marker: Any, path_params: list[str]) -> _Dependency:
    call = marker.dependency
    label = getattr(call, "__qualname__", repr(call))
    specs = analyze(call, path_params, f"dependency {label}")
    for spec in specs:
        if spec.source in ("body", "form", "file"):
            raise ConfigurationError(
                f"dependency {label}: parameter '{spec.name}' reads the request body; "
                "dependencies take the request, path, query, headers, cookies and other "
                "dependencies"
            )
    if inspect.isasyncgenfunction(call):
        kind = "async_generator"
    elif inspect.isgeneratorfunction(call):
        kind = "generator"
    elif inspect.iscoroutinefunction(call) or inspect.iscoroutinefunction(
        getattr(call, "__call__", None)  # noqa: B004 - callable objects
    ):
        kind = "async"
    else:
        kind = "sync"
    return _Dependency(call, specs, _extractor(specs, collect=True), marker.use_cache, kind)


_OVERRIDDEN: dict[tuple[Any, bool, tuple[str, ...]], _Dependency] = {}


def _override(
    dependency: _Dependency, overrides: dict[Any, Any] | None, path_params: list[str]
) -> _Dependency:
    """The dependency to run in place of ``dependency`` (``app.dependency_overrides``)."""
    if not overrides or dependency.call not in overrides:
        return dependency
    replacement = overrides[dependency.call]
    key = (replacement, dependency.use_cache, tuple(path_params))
    resolved = _OVERRIDDEN.get(key)
    if resolved is None:
        from ferro.params import Depends

        resolved = _dependency(Depends(replacement, use_cache=dependency.use_cache), path_params)
        _OVERRIDDEN[key] = resolved
    return resolved


async def _solve(
    dependency: _Dependency,
    request: Any,
    stack: contextlib.AsyncExitStack,
    cache: dict[Any, Any],
    tasks: Any,
    problems: Problems,
    get_executor: Callable[[], Executor],
    overrides: dict[Any, Any] | None = None,
    path_params: list[str] | None = None,
) -> Any:
    """The value of a dependency for this request (problems in its parameters are collected;
    the dependency is then not called). Results are cached under the original function, so an
    override used twice also runs once."""
    if dependency.use_cache and dependency.call in cache:
        return cache[dependency.call]
    original = dependency.call
    dependency = _override(dependency, overrides, path_params or [])
    before = len(problems)
    kwargs, own = dependency.extract(request)
    problems.extend(own)
    for spec in dependency.specs:
        if spec.source == "depends":
            kwargs[spec.name] = await _solve(
                spec.dependency,
                request,
                stack,
                cache,
                tasks,
                problems,
                get_executor,
                overrides,
                path_params,
            )
        elif spec.source == "background":
            kwargs[spec.name] = tasks
    if len(problems) > before:
        return None
    call = dependency.call
    if dependency.kind == "async_generator":
        value = await stack.enter_async_context(contextlib.asynccontextmanager(call)(**kwargs))
    elif dependency.kind == "generator":
        value = stack.enter_context(contextlib.contextmanager(call)(**kwargs))
    elif dependency.kind == "async":
        value = await call(**kwargs)
    else:
        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()
        value = await loop.run_in_executor(
            get_executor(), context.run, functools.partial(call, **kwargs)
        )
    if dependency.use_cache:
        cache[original] = value
    return value


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

    async def read(request: Any, form: Any = None) -> tuple[Any, Problems]:
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
            if media in (_FORM, _MULTIPART):
                from ferro.uploads import UploadFile

                if form is None:
                    form = await request.form()
                data = {}
                for key in form:
                    values = [v for v in form.getlist(key) if not isinstance(v, UploadFile)]
                    if values:
                        data[key] = values if key in sequences else values[-1]
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
            if spec.source in _RICH:
                continue
            if spec.source == "header":
                raw = request.headers.get(spec.key)
            elif spec.source == "cookie":
                raw = request.cookies.get(spec.key)
            elif spec.source == "path":
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
                            "param": spec.key,
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
                    {"param": spec.key, "location": spec.source, "problem": str(problem)}
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


def _form_values(
    specs: list[ParamSpec], form: Any, kwargs: dict[str, Any], problems: Problems
) -> None:
    """Fills ``Form`` and ``UploadFile`` parameters from a parsed form."""
    from ferro.uploads import UploadFile

    for spec in specs:
        if spec.source == "file":
            files = [v for v in form.getlist(spec.key) if isinstance(v, UploadFile)]
            if not files and spec.required:
                problems.append(
                    {"param": spec.key, "location": "form", "problem": "missing required file"}
                )
                continue
            kwargs[spec.name] = files if spec.many else (files[-1] if files else None)
        elif spec.source == "form":
            values = [v for v in form.getlist(spec.key) if isinstance(v, str)]
            if not values:
                if spec.required:
                    problems.append(
                        {
                            "param": spec.key,
                            "location": "form",
                            "problem": "missing required form field",
                        }
                    )
                else:
                    kwargs[spec.name] = spec.default
                continue
            try:
                raw = values[-1]
                kwargs[spec.name] = raw if spec.converter is None else spec.converter(raw)
            except _Problem as problem:
                problems.append({"param": spec.key, "location": "form", "problem": str(problem)})


async def _read_form(request: Any) -> Any:
    from ferro.uploads import FormData

    body = await request.body()
    media = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if not body and media not in (_FORM, _MULTIPART):
        return FormData([])
    if media not in (_FORM, _MULTIPART):
        raise HTTPException(
            415, f"Expected a form body (multipart/form-data or {_FORM}), got {media or 'none'}"
        )
    form = await request.form()
    if not isinstance(form, FormData):
        form = FormData([(key, value) for key in form for value in form.getlist(key)])
    return form


def _rich_endpoint(
    handler: Callable[..., Any],
    specs: list[ParamSpec],
    get_executor: Callable[[], Executor],
    overrides: dict[Any, Any] | None = None,
    path_params: list[str] | None = None,
) -> Endpoint:
    """Handlers with a body, form fields, files, dependencies or background tasks: every
    problem in one 422 (Features 019 and 025)."""
    from ferro.background import BackgroundTasks

    collect = _extractor(specs, collect=True)
    body = next((spec for spec in specs if spec.source == "body"), None)
    read_body = _body_reader(body) if body is not None else None
    form_specs = [spec for spec in specs if spec.source in ("form", "file")]
    dependencies = [spec for spec in specs if spec.source == "depends"]
    background = [spec for spec in specs if spec.source == "background"]
    uses_tasks = bool(background) or any(_uses_tasks(spec.dependency) for spec in dependencies)
    is_async = inspect.iscoroutinefunction(handler)

    async def endpoint(request: Any) -> Any:
        kwargs, problems = collect(request)
        form = None
        if form_specs:
            form = await _read_form(request)
            _form_values(form_specs, form, kwargs, problems)
        if read_body is not None:
            value, body_problems = await read_body(request, form)
            problems.extend(body_problems)
            kwargs[body.name] = value  # type: ignore[union-attr]
        tasks = BackgroundTasks() if uses_tasks else None
        for spec in background:
            kwargs[spec.name] = tasks
        async with contextlib.AsyncExitStack() as stack:
            cache: dict[Any, Any] = {}
            for spec in dependencies:
                kwargs[spec.name] = await _solve(
                    spec.dependency,
                    request,
                    stack,
                    cache,
                    tasks,
                    problems,
                    get_executor,
                    overrides,
                    path_params,
                )
            if problems:
                raise ValidationError(problems)
            if is_async:
                result = await handler(**kwargs)
            else:
                loop = asyncio.get_running_loop()
                context = contextvars.copy_context()
                call = functools.partial(handler, **kwargs)
                result = await loop.run_in_executor(get_executor(), context.run, call)
        if tasks:
            tasks.schedule()
        return result

    return endpoint


def _uses_tasks(dependency: _Dependency) -> bool:
    return any(
        spec.source == "background" or (spec.source == "depends" and _uses_tasks(spec.dependency))
        for spec in dependency.specs
    )


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
    overrides: dict[Any, Any] | None = None,
    status_code: int | None = None,
) -> Endpoint:
    """``overrides`` is the app's ``dependency_overrides`` (read at each request);
    ``status_code`` is the status of results that are not ``Response`` objects."""
    endpoint = _call_endpoint(handler, path_params, name, get_executor, overrides)
    respond = _response_adapter(handler, name)
    if respond is None and status_code is None:
        return endpoint
    from ferro.response import Response

    async def converted(request: Any) -> Any:
        result = await endpoint(request)
        if isinstance(result, Response):
            return result  # the handler chose its status
        if respond is not None:
            result = respond(result)
        if status_code is None:
            return result
        if isinstance(result, Response):
            result.status = status_code
            return result
        return Response(None if status_code == 204 else result, status=status_code)

    return converted


def _call_endpoint(
    handler: Callable[..., Any],
    path_params: list[str],
    name: str,
    get_executor: Callable[[], Executor],
    overrides: dict[Any, Any] | None = None,
) -> Endpoint:
    specs = analyze(handler, path_params, name)
    if any(spec.source in _RICH for spec in specs):
        return _rich_endpoint(handler, specs, get_executor, overrides, path_params)
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
