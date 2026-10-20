"""The application object and route decorators."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterable
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Any, NamedTuple, TypeVar

from ferro import _ferro
from ferro.exceptions import ConfigurationError
from ferro.middleware import Middleware
from ferro.routing import Endpoint, build_endpoint
from ferro.security import CORS

__all__ = ["App", "RouteInfo"]

H = TypeVar("H", bound=Callable[..., Any])

_RATE = re.compile(r"^\s*([1-9][0-9]*)\s*/\s*(second|sec|minute|min|hour|day)s?\s*$", re.I)


def check_rate_limit(text: str, where: str) -> str:
    if not isinstance(text, str) or not _RATE.match(text):
        raise ConfigurationError(
            f"{where}: invalid rate limit {text!r}: expected N/second, N/minute, N/hour or "
            "N/day with N >= 1"
        )
    return text.strip()


_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")


class RouteInfo(NamedTuple):
    methods: tuple[str, ...]
    path: str
    name: str


@dataclass(frozen=True)
class RouteRecord:
    methods: tuple[str, ...]
    path: str
    handler: Callable[..., Any]
    endpoint: Endpoint
    name: str
    rate_limit: str | None = None
    csrf: bool = True
    websocket: bool = False


def _qualified_name(handler: Callable[..., Any]) -> str:
    module = getattr(handler, "__module__", None) or "<unknown>"
    qualname = getattr(handler, "__qualname__", None) or repr(handler)
    return f"{module}.{qualname}"


def _normalize_methods(path: str, methods: Iterable[str]) -> tuple[str, ...]:
    if isinstance(methods, str):
        methods = [methods]
    normalized: list[str] = []
    for method in methods:
        upper = str(method).upper()
        if upper == "HEAD":
            raise ConfigurationError(
                f"route {path!r}: HEAD cannot be registered; GET routes answer HEAD automatically"
            )
        if upper not in _METHODS:
            raise ConfigurationError(
                f"route {path!r}: unsupported method {method!r} (use {', '.join(_METHODS)})"
            )
        if upper not in normalized:
            normalized.append(upper)
    if not normalized:
        raise ConfigurationError(f"route {path!r}: at least one method is required")
    return tuple(normalized)


class App:
    """A Ferro application: routes plus settings.

    Settings given here are the lowest-priority explicit source; environment variables
    (``FERRO_*``) and command-line options override them.
    """

    def __init__(
        self,
        *,
        debug: bool | None = None,
        host: str | None = None,
        port: int | None = None,
        workers: int | None = None,
        max_body_size: int | None = None,
        request_timeout: float | None = None,
        shutdown_timeout: float | None = None,
        log_level: str | None = None,
        max_connections: int | None = None,
        header_timeout: float | None = None,
        sync_threads: int | None = None,
        database_url: str | None = None,
        csrf: bool | None = None,
        trusted_origins: Iterable[str] | None = None,
        trusted_proxies: Iterable[str] | None = None,
        rate_limit: str | None = None,
        cors: CORS | None = None,
        hsts: bool | None = None,
        hsts_max_age: int | None = None,
        content_security_policy: str | None = None,
        processes: int | None = None,
        health_path: str | None = None,
        ready_path: str | None = None,
        metrics: bool | None = None,
        metrics_path: str | None = None,
        metrics_allow: Iterable[str] | None = None,
        log_format: str | None = None,
        shutdown_drain: float | None = None,
        tls_cert: str | None = None,
        tls_key: str | None = None,
        cache: str | None = None,
        broadcast: str | None = None,
    ) -> None:
        given = {
            "debug": debug,
            "host": host,
            "port": port,
            "workers": workers,
            "max_body_size": max_body_size,
            "request_timeout": request_timeout,
            "shutdown_timeout": shutdown_timeout,
            "log_level": log_level,
            "max_connections": max_connections,
            "header_timeout": header_timeout,
            "sync_threads": sync_threads,
            "database_url": database_url,
            "csrf": csrf,
            "trusted_origins": None if trusted_origins is None else list(trusted_origins),
            "trusted_proxies": None if trusted_proxies is None else list(trusted_proxies),
            "rate_limit": rate_limit,
            "cors": cors,
            "hsts": hsts,
            "hsts_max_age": hsts_max_age,
            "content_security_policy": content_security_policy,
            "processes": processes,
            "health_path": health_path,
            "ready_path": ready_path,
            "metrics": metrics,
            "metrics_path": metrics_path,
            "metrics_allow": None if metrics_allow is None else list(metrics_allow),
            "log_format": log_format,
            "shutdown_drain": shutdown_drain,
            "tls_cert": tls_cert,
            "tls_key": tls_key,
            "cache": cache,
            "broadcast": broadcast,
        }
        self._settings: dict[str, Any] = {k: v for k, v in given.items() if v is not None}
        if cache == "database":
            # Registers the cache table so `makemigrations` creates it (Feature 015).
            import ferro.cache.models  # noqa: F401
        self._routes: list[RouteRecord] = []
        self._middleware: list[Middleware] = []
        self._executor: Executor | None = None

    # -- registration -----------------------------------------------------------------------

    def middleware(self, func: Middleware) -> Middleware:
        """Registers ``async def middleware(request, call_next)``; the first registered runs
        outermost. Returns the function unchanged."""
        if not inspect.iscoroutinefunction(func):
            raise TypeError(
                f"middleware {getattr(func, '__qualname__', func)!r} must be an async def "
                "function taking (request, call_next)"
            )
        self._middleware.append(func)
        return func

    def add_auth(self) -> None:
        """Installs the authentication middleware (outermost), so every routed request has
        ``request.user`` and ``request.session``. Needs the auth tables (``ferro migrate``)."""
        from ferro.auth.sessions import auth_middleware

        if auth_middleware not in self._middleware:
            self._middleware.insert(0, auth_middleware)

    def add_admin(self, site: Any = None, prefix: str = "/admin") -> None:
        """Mounts the admin (``ferro.admin.admin`` by default) under ``prefix`` and installs the
        authentication it needs."""
        if site is None:
            from ferro.admin import admin as site
        self.add_auth()
        site.mount(self, prefix)

    def route(
        self,
        path: str,
        methods: Iterable[str],
        *,
        name: str | None = None,
        rate_limit: str | None = None,
        csrf: bool = True,
    ) -> Callable[[H], H]:
        """Registers a handler for ``path`` and ``methods``. Returns the handler unchanged.

        ``rate_limit`` (``"5/minute"``) replaces the app-wide limit for this route; ``csrf=False``
        exempts it from the cross-origin check (e.g. webhooks called by other services).
        """
        normalized = _normalize_methods(path, methods)
        if rate_limit is not None:
            rate_limit = check_rate_limit(rate_limit, f"route {path!r}")
        try:
            params = _ferro.parse_path(path)
        except (ValueError, TypeError) as exc:
            raise ConfigurationError(str(exc)) from None

        def decorator(handler: H) -> H:
            if not callable(handler):
                raise ConfigurationError(
                    f"route {path!r}: handler must be callable, got {handler!r}"
                )
            handler_name = name or _qualified_name(handler)
            endpoint = build_endpoint(handler, params, handler_name, self._get_executor)
            self._routes.append(
                RouteRecord(normalized, path, handler, endpoint, handler_name, rate_limit, csrf)
            )
            return handler

        return decorator

    def websocket(
        self,
        path: str,
        *,
        name: str | None = None,
        rate_limit: str | None = None,
        csrf: bool = True,
    ) -> Callable[[H], H]:
        """Registers an ``async def handler(ws, ...)`` for WebSocket connections at ``path``
        (Feature 018). ``ws`` is a :class:`ferro.websockets.WebSocket`; other parameters come
        from the path and the query string as for HTTP routes. The handshake is rate limited
        and checked like an unsafe request (``csrf=False`` exempts it). With ``add_auth()``,
        ``ws.user`` and ``ws.session`` are loaded from the session cookie."""
        if rate_limit is not None:
            rate_limit = check_rate_limit(rate_limit, f"route {path!r}")
        try:
            params = _ferro.parse_path(path)
        except (ValueError, TypeError) as exc:
            raise ConfigurationError(str(exc)) from None

        def decorator(handler: H) -> H:
            from ferro.routing import build_websocket_endpoint

            handler_name = name or _qualified_name(handler)
            endpoint = build_websocket_endpoint(handler, params, handler_name, self._load_user)
            self._routes.append(
                RouteRecord(("GET",), path, handler, endpoint, handler_name, rate_limit, csrf, True)
            )
            return handler

        return decorator

    async def _load_user(self, ws: Any) -> None:
        """Gives a WebSocket the session's user when authentication is installed."""
        from ferro.auth.sessions import auth_middleware, read_session

        if auth_middleware in self._middleware:
            ws.user, ws.session = await read_session(ws.cookies)

    def get(self, path: str, **options: Any) -> Callable[[H], H]:
        return self.route(path, ["GET"], **options)

    def post(self, path: str, **options: Any) -> Callable[[H], H]:
        return self.route(path, ["POST"], **options)

    def put(self, path: str, **options: Any) -> Callable[[H], H]:
        return self.route(path, ["PUT"], **options)

    def patch(self, path: str, **options: Any) -> Callable[[H], H]:
        return self.route(path, ["PATCH"], **options)

    def delete(self, path: str, **options: Any) -> Callable[[H], H]:
        return self.route(path, ["DELETE"], **options)

    # -- introspection ----------------------------------------------------------------------

    @property
    def routes(self) -> list[RouteInfo]:
        """Registered routes in registration order."""
        return [RouteInfo(r.methods, r.path, r.name) for r in self._routes]

    @property
    def route_records(self) -> tuple[RouteRecord, ...]:
        return tuple(self._routes)

    # -- serving ----------------------------------------------------------------------------

    def run(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        workers: int | None = None,
        debug: bool | None = None,
    ) -> None:
        """Serves the application like ``ferro run`` until interrupted (blocking)."""
        from ferro import __version__
        from ferro.config import resolve
        from ferro.server import serve

        cli = {"host": host, "port": port, "workers": workers, "debug": debug}
        settings = resolve(cli=cli, app=self._settings, mode="run")
        if settings.processes > 1:
            raise ConfigurationError(
                f"App.run() serves a single process; use ferro run --processes "
                f"{settings.processes} to start several"
            )
        mode = "development mode" if settings.debug else "production mode"
        banner = f"Ferro {__version__} serving on {{url}} ({mode}, {settings.workers} workers)"
        code = serve(self, settings, banner=banner)
        if code:
            raise SystemExit(code)

    def _bind_executor(self, executor: Executor | None) -> None:
        self._executor = executor

    def _get_executor(self) -> Executor:
        if self._executor is None:
            raise RuntimeError("the application is not being served")
        return self._executor
