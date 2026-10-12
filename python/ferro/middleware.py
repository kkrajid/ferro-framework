"""Python middleware (Feature 006).

```python
@app.middleware
async def timing(request, call_next):
    response = await call_next(request)
    response.headers.append(("x-served-by", "ferro"))
    return response
```

Middleware wraps route endpoints once, when the server starts; the first registered is the
outermost. Requests answered by the engine (404, 405, limits, CORS preflights, cross-origin and
rate-limit refusals) never run Python middleware.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ferro.response import Response

__all__ = ["Endpoint", "Middleware", "compose", "to_response"]

Endpoint = Callable[[Any], Awaitable[Any]]
CallNext = Callable[[Any], Awaitable[Response]]
Middleware = Callable[[Any, CallNext], Awaitable[Response]]


def to_response(value: Any) -> Response:
    """A handler result as the ``Response`` the client would receive."""
    if isinstance(value, Response):
        return value
    if value is None:
        return Response(None, status=204)
    return Response(value)


def _name(middleware: Middleware) -> str:
    return getattr(middleware, "__qualname__", None) or repr(middleware)


def _wrap(middleware: Middleware, inner: Endpoint) -> Endpoint:
    name = _name(middleware)

    async def endpoint(request: Any) -> Response:
        called = False

        async def call_next(forwarded: Any) -> Response:
            nonlocal called
            if called:
                raise RuntimeError(f"middleware {name} called call_next() more than once")
            called = True
            return to_response(await inner(forwarded))

        result = await middleware(request, call_next)
        if not isinstance(result, Response):
            raise TypeError(
                f"middleware {name} returned {type(result).__name__}; it must return the "
                "Response from call_next() or another Response"
            )
        return result

    return endpoint


def compose(middlewares: list[Middleware], endpoint: Endpoint) -> Endpoint:
    """``endpoint`` wrapped by ``middlewares`` (the first is outermost)."""
    for middleware in reversed(middlewares):
        endpoint = _wrap(middleware, endpoint)
    return endpoint
