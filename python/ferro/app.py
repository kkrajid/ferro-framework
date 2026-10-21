"""The application object and route decorators."""

from __future__ import annotations

import contextlib
import inspect
import re
from collections.abc import Callable, Iterable, Iterator
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
_NOTHING = object()


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
    #: Replaces the app-wide ``max_body_size`` for this route (uploads; Feature 025).
    max_body_size: int | None = None
    #: Listed in the OpenAPI document (Feature 025).
    include_in_schema: bool = True
    tags: tuple[str, ...] = ()
    #: OpenAPI metadata (Feature 026).
    summary: str | None = None
    description: str | None = None
    status_code: int | None = None
    responses: Any = None
    deprecated: bool = False
    operation_id: str | None = None


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
        hsts_include_subdomains: bool | None = None,
        hsts_preload: bool | None = None,
        allowed_hosts: Iterable[str] | None = None,
        https_redirect: bool | None = None,
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
            "hsts_include_subdomains": hsts_include_subdomains,
            "hsts_preload": hsts_preload,
            "allowed_hosts": None if allowed_hosts is None else list(allowed_hosts),
            "https_redirect": https_redirect,
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
        #: ``{dependency: replacement}``: dependencies to replace at request time, for tests
        #: (Feature 026).
        self.dependency_overrides: dict[Callable[..., Any], Callable[..., Any]] = {}
        self._startup: list[Callable[[], Any]] = []
        self._shutdown: list[Callable[[], Any]] = []
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

    def add_auth(
        self, *, max_login_attempts: int | None = None, login_lockout: float | None = None
    ) -> None:
        """Installs the authentication middleware (outermost), so every routed request has
        ``request.user`` and ``request.session``. Needs the auth tables (``ferro migrate``).

        ``authenticate()`` refuses a username (from one client address) after
        ``max_login_attempts`` failures (default 5) for ``login_lockout`` seconds (default 900).
        """
        from ferro.auth import sessions

        sessions.configure_login_throttle(max_login_attempts, login_lockout)
        if sessions.auth_middleware not in self._middleware:
            self._middleware.insert(0, sessions.auth_middleware)

    def on_startup(self, func: Callable[[], Any]) -> Callable[[], Any]:
        """Registers ``func`` (sync or ``async``, no arguments) to run on the server's event
        loop before it accepts requests — open clients, warm caches. An exception stops the
        server from starting. Runs in each process of ``--processes``, and in ``TestClient``."""
        if not callable(func):
            raise TypeError("on_startup() takes a function")
        self._startup.append(func)
        return func

    def on_shutdown(self, func: Callable[[], Any]) -> Callable[[], Any]:
        """Registers ``func`` to run after the server stopped serving (reverse order of
        registration); exceptions are logged and the others still run."""
        if not callable(func):
            raise TypeError("on_shutdown() takes a function")
        self._shutdown.append(func)
        return func

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
        max_body_size: int | None = None,
        include_in_schema: bool = True,
        tags: Iterable[str] = (),
        summary: str | None = None,
        description: str | None = None,
        status_code: int | None = None,
        responses: dict[int | str, Any] | None = None,
        deprecated: bool = False,
        operation_id: str | None = None,
    ) -> Callable[[H], H]:
        """Registers a handler for ``path`` and ``methods``. Returns the handler unchanged.

        ``rate_limit`` (``"5/minute"``) replaces the app-wide limit for this route; ``csrf=False``
        exempts it from the cross-origin check (e.g. webhooks called by other services);
        ``max_body_size`` (bytes) replaces the app-wide body limit, for uploads;
        ``include_in_schema=False`` leaves it out of the OpenAPI document; ``tags`` group it
        there. A path's last segment may be ``{name:path}``: the rest of the path.

        ``status_code`` is the status of results that are not ``Response`` objects (``201``
        for creations); ``summary``, ``description``, ``responses`` (``{404: "Not found"}`` or
        ``{404: {"description": ..., "model": ErrorModel}}``), ``deprecated`` and
        ``operation_id`` describe the route in the OpenAPI document.
        """
        if status_code is not None and (
            isinstance(status_code, bool)
            or not isinstance(status_code, int)
            or not 100 <= status_code <= 599
        ):
            raise ConfigurationError(f"route {path!r}: invalid status_code {status_code!r}")
        normalized = _normalize_methods(path, methods)
        if rate_limit is not None:
            rate_limit = check_rate_limit(rate_limit, f"route {path!r}")
        if max_body_size is not None and (
            isinstance(max_body_size, bool)
            or not isinstance(max_body_size, int)
            or not 1 <= max_body_size <= 1 << 34
        ):
            raise ConfigurationError(
                f"route {path!r}: max_body_size must be a number of bytes from 1 to 16 GiB"
            )
        tags = tuple(tags) if not isinstance(tags, str) else (tags,)
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
            endpoint = build_endpoint(
                handler,
                params,
                handler_name,
                self._get_executor,
                self.dependency_overrides,
                status_code,
            )
            self._routes.append(
                RouteRecord(
                    normalized,
                    path,
                    handler,
                    endpoint,
                    handler_name,
                    rate_limit,
                    csrf,
                    max_body_size=max_body_size,
                    include_in_schema=include_in_schema,
                    tags=tags,
                    summary=summary,
                    description=description,
                    status_code=status_code,
                    responses=responses,
                    deprecated=deprecated,
                    operation_id=operation_id,
                )
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
                RouteRecord(
                    ("GET",),
                    path,
                    handler,
                    endpoint,
                    handler_name,
                    rate_limit,
                    csrf,
                    websocket=True,
                    include_in_schema=False,
                )
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

    # -- files, URLs, documentation (Feature 025) --------------------------------------------

    def static(
        self,
        prefix: str,
        directory: str | Any,
        *,
        name: str = "static",
        html: bool = False,
        cache_max_age: int | None = 3600,
    ) -> None:
        """Serves the files below ``directory`` at ``prefix`` (``app.static("/static",
        "static")``): conditional requests, ranges, ``Cache-Control: max-age=cache_max_age``.
        ``url_for("static", path="css/site.css")`` builds their addresses. Behind a proxy or CDN,
        serving them there is faster still."""
        from ferro.files import StaticFiles

        prefix = "/" + prefix.strip("/")
        files = StaticFiles(directory, html=html, cache_max_age=cache_max_age)

        async def serve_static(request, path: str):
            return await files(request, path)

        self.route(f"{prefix}/{{path:path}}", ["GET"], name=name, include_in_schema=False)(
            serve_static
        )

    @contextlib.contextmanager
    def override_dependency(
        self, dependency: Callable[..., Any], replacement: Callable[..., Any]
    ) -> Iterator[None]:
        """Replaces ``dependency`` with ``replacement`` inside the block (tests)::

        with app.override_dependency(current_user, lambda: fake_user):
            ...
        """
        previous = self.dependency_overrides.get(dependency, _NOTHING)
        self.dependency_overrides[dependency] = replacement
        try:
            yield
        finally:
            if previous is _NOTHING:
                self.dependency_overrides.pop(dependency, None)
            else:
                self.dependency_overrides[dependency] = previous

    def url_for(self, name: str, /, **params: Any) -> str:
        """The path of the route named ``name`` with its path parameters filled in; other
        ``params`` become the query string. ``url_for("product", id=7, ref="mail")`` →
        ``/products/7?ref=mail``."""
        from urllib.parse import quote, urlencode

        record = next((r for r in self._routes if r.name == name), None)
        if record is None:
            known = ", ".join(sorted(r.name for r in self._routes)) or "none"
            raise ConfigurationError(f"url_for(): no route named {name!r}; routes: {known}")
        path = record.path
        for placeholder in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)(:path)?\}", path):
            key, tail = placeholder
            if key not in params:
                raise ConfigurationError(f"url_for({name!r}): missing path parameter {key!r}")
            value = str(params.pop(key))
            encoded = quote(value, safe="/" if tail else "")
            path = path.replace("{" + key + (":path" if tail else "") + "}", encoded)
        if params:
            path += "?" + urlencode(params, doseq=True)
        return path

    def add_openapi(
        self,
        *,
        title: str = "API",
        version: str = "0.1.0",
        description: str | None = None,
        path: str = "/openapi.json",
        docs_path: str | None = "/docs",
        servers: list[dict[str, str]] | None = None,
    ) -> None:
        """Serves an OpenAPI 3.1 document of the routes at ``path`` and Swagger UI at
        ``docs_path`` (``None``: no page). Parameters, Pydantic bodies and response models,
        uploads and the 422 error format are described from the handlers' signatures."""
        from ferro.openapi import docs_page, openapi_document
        from ferro.response import HTMLResponse, JSONResponse

        cached: dict[str, Any] = {}

        async def openapi() -> Any:
            if "document" not in cached:
                cached["document"] = openapi_document(
                    self, title=title, version=version, description=description, servers=servers
                )
            return JSONResponse(cached["document"])

        self.route(path, ["GET"], name="openapi", include_in_schema=False)(openapi)
        if docs_path is not None:
            page = docs_page(title, path)

            async def docs() -> Any:
                return HTMLResponse(
                    page,
                    headers={
                        "content-security-policy": (
                            "default-src 'none'; script-src https://cdn.jsdelivr.net "
                            "'unsafe-inline'; style-src https://cdn.jsdelivr.net 'unsafe-inline'; "
                            "img-src 'self' data: https://cdn.jsdelivr.net; connect-src 'self'"
                        )
                    },
                )

            self.route(docs_path, ["GET"], name="docs", include_in_schema=False)(docs)

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
