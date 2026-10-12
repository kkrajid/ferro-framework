"""Builds the endpoint the native server calls for each route: ``endpoint(request) -> coroutine``.

Handler parameters are filled by name (FR-007): ``request`` (or any parameter annotated
``Request``) receives the request, names matching a path parameter receive the path value, and
every other parameter receives the query parameter of the same name. ``int``, ``float``, ``bool``
and ``str`` annotations (optionally ``X | None``) are converted; failures become one
``ValidationError`` (422) listing every problem (FR-008).
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import types
import typing
from collections.abc import Callable, Coroutine
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Any, Literal

from ferro._ferro import Request
from ferro.exceptions import ConfigurationError, ValidationError

__all__ = ["ParamSpec", "analyze", "build_endpoint"]

Endpoint = Callable[[Any], Coroutine[Any, Any, Any]]
Source = Literal["request", "path", "query"]

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


def analyze(
    handler: Callable[..., Any], path_params: list[str], handler_name: str
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
        if annotation is Request or (name == "request" and annotation is inspect.Parameter.empty):
            specs.append(ParamSpec(name, "request", None, True, None))
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


def _extractor(specs: list[ParamSpec]) -> Callable[[Any], dict[str, Any]]:
    def extract(request: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        problems: list[dict[str, str]] = []
        path_values = None
        query = None
        for spec in specs:
            if spec.source == "request":
                kwargs[spec.name] = request
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


def build_endpoint(
    handler: Callable[..., Any],
    path_params: list[str],
    name: str,
    get_executor: Callable[[], Executor],
) -> Endpoint:
    specs = analyze(handler, path_params, name)
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
