"""Runs an application: Python execution thread, sync executor, native server, signals."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from ferro import _ferro, db, log
from ferro.config import Settings
from ferro.exceptions import ConfigurationError

if TYPE_CHECKING:
    from ferro.app import App

__all__ = ["ServerRunner", "serve"]

_WAIT_SLICE = 0.25


class _Shutdown(BaseException):
    """Raised in the main thread by SIGTERM/SIGBREAK handlers. A ``BaseException``, like
    ``KeyboardInterrupt``: code that handles ordinary errors (logging handlers catch
    ``Exception``) must not swallow a stop request."""


class ServerRunner:
    """Owns one running server. ``serve`` uses it for the CLI; tests use it directly."""

    def __init__(
        self,
        app: App,
        settings: Settings,
        *,
        listener: int | None = None,
        shared_dir: str | None = None,
    ) -> None:
        self.app = app
        self._listener = listener
        self.settings = settings
        self.address: tuple[str, int] | None = None
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop, name="ferro-python", daemon=True
        )
        self._executor = ThreadPoolExecutor(
            max_workers=settings.sync_threads, thread_name_prefix="ferro-sync"
        )
        from ferro.middleware import compose
        from ferro.security import set_secure_cookie_default

        set_secure_cookie_default(not settings.debug)
        _check_reserved_paths(app, settings)
        signalled = _request_signals_connected()
        routes = []
        for route in app.route_records:
            endpoint = route.endpoint
            if not route.websocket and app._middleware:
                endpoint = compose(app._middleware, endpoint)
            if not route.websocket and signalled:
                endpoint = _with_request_signals(app, endpoint)
            routes.append(
                (
                    list(route.methods),
                    route.path,
                    endpoint,
                    route.name,
                    route.rate_limit,
                    route.csrf,
                    route.websocket,
                )
            )
        try:
            native = settings.to_native()
            if shared_dir is not None:
                # State shared with the other workers of `--processes N` (Feature 015).
                native["shared_state"] = os.path.join(shared_dir, "rates.shm")
                native["metrics_dir"] = os.path.join(shared_dir, "metrics")
            self._native = _ferro.Server(routes, native, self._loop)
        except ValueError as exc:
            self._executor.shutdown(wait=False)
            self._loop.close()
            raise ConfigurationError(str(exc)) from None
        self._closed = False
        self._owns_database = False

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def start(self) -> tuple[str, int]:
        """Connects the database (if configured) and starts serving. Raises ``OSError`` if the
        address cannot be bound."""
        log.configure(self.settings.log_level, self.settings.log_format)
        from ferro import cache

        cache.apply_setting(self.settings.cache)
        from ferro import broadcast

        broadcast.apply_setting(self.settings.broadcast)
        try:
            if self.settings.database_url is not None:
                db.connect_now(self.settings.database_url)
                self._owns_database = True
            self.app._bind_executor(self._executor)
            self._loop_thread.start()
            self._run_hooks(self.app._startup, startup=True)
            self._started_hooks = True
            self.address = self._native.start(self._listener)
        except BaseException:
            self.close()
            raise
        return self.address

    def _run_hooks(self, hooks: list, *, startup: bool) -> None:
        """Runs ``on_startup``/``on_shutdown`` functions on the server's loop, in order."""
        if not hooks:
            return
        import inspect

        async def run() -> None:
            for hook in hooks:
                try:
                    result = hook()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    if startup:
                        raise
                    logging.getLogger("ferro.server").exception(
                        "on_shutdown function %r failed", hook
                    )

        timeout = None if startup else self.settings.shutdown_timeout or None
        asyncio.run_coroutine_threadsafe(run(), self._loop).result(timeout)

    def wait(self, timeout: float | None = None) -> bool:
        return self._native.wait(timeout)

    def stop(self, graceful: bool = True) -> None:
        self._native.stop(graceful)

    def begin_drain(self) -> None:
        """Readiness fails from now on; requests are still served (Feature 011)."""
        self._native.begin_drain()

    def wake_acceptor(self) -> None:
        """Releases an accept loop stuck on a listening socket shared with other processes.

        On Windows, a non-blocking accept that races with another process for the same
        connection can block until the next connection arrives, and an accept loop blocked
        there never sees the stop request (Feature 010). One throwaway connection to the
        shared port releases it."""
        if self._listener is None or self.address is None or sys.platform != "win32":
            return
        host, port = self.address
        host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
        with contextlib.suppress(OSError):
            socket.create_connection((host, port), timeout=0.5).close()

    def close(self) -> None:
        """Stops the Python execution thread and the sync executor (after the server stopped)."""
        if self._closed:
            return
        self._closed = True
        if getattr(self, "_started_hooks", False) and self._loop_thread.is_alive():
            self._run_hooks(list(reversed(self.app._shutdown)), startup=False)
        if self._loop_thread.is_alive():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=5)
        if not self._loop.is_running():
            self._loop.close()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.app._bind_executor(None)
        from ferro.broadcast import broadcast

        broadcast._stop()  # its listener belongs to this server's event loop
        if self._owns_database:
            db.disconnect_now()
            self._owns_database = False


def _request_signals_connected() -> bool:
    from ferro import signals

    return any(
        s._receivers
        for s in (signals.request_started, signals.request_finished, signals.got_request_exception)
    )


def _with_request_signals(app: App, endpoint):
    """Sends ``request_started``/``request_finished``/``got_request_exception`` around a routed
    request (only when receivers were connected before the server started)."""
    import inspect

    from ferro import signals
    from ferro.exceptions import HTTPException

    async def signalled(request):
        await signals.request_started.send(app, request=request)
        try:
            result = endpoint(request)
            if inspect.isawaitable(result):
                result = await result
            return result
        except HTTPException:
            raise
        except Exception as exc:
            await signals.got_request_exception.send_robust(app, request=request, exception=exc)
            raise
        finally:
            await signals.request_finished.send_robust(app, request=request)

    return signalled


def _drain(runner: ServerRunner, seconds: float) -> bool:
    """Keeps serving with failing readiness for ``seconds`` so load balancers stop sending
    traffic first. Returns ``True`` if the server stopped on its own meanwhile."""
    if seconds <= 0:
        return False
    runner.begin_drain()
    logging.getLogger("ferro.server").info(
        "draining for %g s: readiness reports not ready; stopping afterwards", seconds
    )
    deadline = time.monotonic() + seconds
    while (remaining := deadline - time.monotonic()) > 0:
        if runner.wait(min(_WAIT_SLICE, remaining)):
            return True
    return False


def _check_reserved_paths(app: App, settings: Settings) -> None:
    """Routes may not use the addresses the engine answers itself (Feature 011)."""
    reserved = [
        ("health_path", settings.health_path, "the liveness probe", "disable it"),
        ("ready_path", settings.ready_path, "the readiness probe", "disable it"),
    ]
    if settings.metrics:
        reserved.append(("metrics_path", settings.metrics_path, "the metrics", "turn metrics off"))
    for route in app.route_records:
        for setting, path, what, alternative in reserved:
            if path and (route.path.rstrip("/") or "/") == (path.rstrip("/") or "/"):
                if alternative == "disable it":
                    alternative = f'disable it with {setting}=""'
                raise ConfigurationError(
                    f"route {route.path!r} (handler {route.name}) uses {what}' address; "
                    f"move it with {setting} or {alternative}"
                )


def _warn_debug_build() -> None:
    """An unoptimized native module (``maturin develop`` without ``--release``) is several times
    slower; say so whenever it serves (Feature 020). Logged, so ``--log-format json`` stays JSON."""
    if getattr(_ferro, "BUILD_PROFILE", "release") == "debug":
        logging.getLogger("ferro.server").warning(
            "Ferro's native module is an unoptimized debug build (`maturin develop`); it is "
            "several times slower. Use `maturin develop --release` or the published wheel for "
            "benchmarks and production."
        )


def _warn_open_hosts(settings: Settings) -> None:
    """Outside development, a server reachable from other machines should list its names
    (Feature 023): otherwise any ``Host`` header is accepted."""
    loopback = settings.host in ("127.0.0.1", "::1", "localhost")
    if not settings.debug and not loopback and not settings.allowed_hosts:
        logging.getLogger("ferro.server").warning(
            "allowed_hosts is not set: requests with any Host header are served. Set "
            "FERRO_ALLOWED_HOSTS=example.com (or App(allowed_hosts=[...])) in production."
        )


def _raise_shutdown(signum: int, frame: object) -> None:
    raise _Shutdown(signum)


def _install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, _raise_shutdown)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, _raise_shutdown)


def serve(
    app: App,
    settings: Settings,
    *,
    banner: str | None = None,
    listener: int | None = None,
    shared_dir: str | None = None,
) -> int:
    """Serves until SIGINT/SIGTERM (SIGBREAK on Windows). Returns the process exit code.

    Must run on the main thread: Python only delivers signals there. ``listener`` is a
    listening socket handed over by the multi-process supervisor (Feature 010).
    """
    runner = ServerRunner(app, settings, listener=listener, shared_dir=shared_dir)
    # Before starting: a stop request during startup (e.g. right after a deploy) must end the
    # process cleanly, not through the signal's default action.
    _install_signal_handlers()
    try:
        try:
            host, port = runner.start()
        except (KeyboardInterrupt, _Shutdown):
            # Stopped while starting: the server may already be running.
            with contextlib.suppress(Exception):
                runner.stop(graceful=False)
                runner.wait(5)
            return 0
        try:
            # Inside the handlers' reach: a stop request arriving now takes the normal path.
            if banner is not None:
                shown_host = f"[{host}]" if ":" in host else host
                url = f"{settings.scheme}://{shown_host}:{port}"
                print(banner.format(url=url), file=sys.stderr, flush=True)
            _warn_debug_build()
            _warn_open_hosts(settings)
            while not runner.wait(_WAIT_SLICE):
                pass
            return 0
        except (KeyboardInterrupt, _Shutdown):
            pass
        try:
            if _drain(runner, settings.shutdown_drain):
                return 0
            runner.stop(graceful=True)
            while not runner.wait(_WAIT_SLICE):
                runner.wake_acceptor()
            return 0
        except (KeyboardInterrupt, _Shutdown):
            runner.stop(graceful=False)
            for _ in range(int(5 / _WAIT_SLICE)):
                if runner.wait(_WAIT_SLICE):
                    break
                runner.wake_acceptor()
            return 1
    finally:
        runner.close()
