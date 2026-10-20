"""Settings resolution: command line > environment (FERRO_*) > App(...) > default."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ferro.exceptions import ConfigurationError

__all__ = ["SETTING_NAMES", "Settings", "resolve"]

Mode = Literal["dev", "run"]

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_LOG_LEVELS = {"trace", "debug", "info", "warning", "error"}
_LOG_FORMATS = ("text", "json")


class _Invalid(ValueError):
    pass


def _int(minimum: int, maximum: int) -> Callable[[Any], int]:
    def convert(value: Any) -> int:
        if isinstance(value, bool):
            raise _Invalid("expected an integer")
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise _Invalid("expected an integer") from None
        if isinstance(value, float) and value != number:
            raise _Invalid("expected an integer")
        if not minimum <= number <= maximum:
            raise _Invalid(f"must be between {minimum} and {maximum}")
        return number

    return convert


def _seconds(minimum: float, maximum: float) -> Callable[[Any], float]:
    def convert(value: Any) -> float:
        if isinstance(value, bool):
            raise _Invalid("expected a number of seconds")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise _Invalid("expected a number of seconds") from None
        if not minimum <= number <= maximum:
            raise _Invalid(f"must be between {minimum} and {maximum} seconds")
        return number

    return convert


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise _Invalid("expected one of 1/0, true/false, yes/no, on/off")


def _host(value: Any) -> str:
    text = str(value).strip()
    if not text or any(ch.isspace() for ch in text):
        raise _Invalid("must be a non-empty IP address or hostname")
    return text


def _database_url(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        raise _Invalid("must not be empty")
    return text


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    else:
        try:
            items = [str(item).strip() for item in value]
        except TypeError:
            raise _Invalid("expected a list or a comma-separated string") from None
    return tuple(item for item in items if item)


def _rate_limit(value: Any) -> str | None:
    if value is None:
        return None
    from ferro.app import check_rate_limit

    try:
        return check_rate_limit(str(value), "rate_limit")
    except ConfigurationError as error:
        raise _Invalid(str(error).split(": ", 1)[1]) from None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _cors(value: Any) -> Any:
    from ferro.security import CORS

    if value is None or isinstance(value, CORS):
        return value
    raise _Invalid("expected ferro.CORS(...) (set it with App(cors=CORS(...)))")


def _path(value: Any) -> str:
    """An address served by the engine itself (probes, metrics); empty disables it."""
    text = str(value).strip()
    if text and (not text.startswith("/") or any(ch.isspace() or ch in "{}?#" for ch in text)):
        raise _Invalid("must be empty or a path starting with '/' (no spaces, braces, ? or #)")
    return text


def _addresses(value: Any) -> tuple[str, ...]:
    entries = _strings(value)
    for entry in entries:
        try:
            ipaddress.ip_network(entry, strict=False)
        except ValueError:
            raise _Invalid(f"{entry!r} is not an IP address or network") from None
    return entries


def _log_format(value: Any) -> str:
    text = str(value).strip().lower()
    if text not in _LOG_FORMATS:
        raise _Invalid(f"must be one of {', '.join(_LOG_FORMATS)}")
    return text


_CACHES = ("memory", "database")


def _cache(value: Any) -> str:
    text = str(value).strip().lower()
    if text not in _CACHES:
        raise _Invalid(f"must be one of {', '.join(_CACHES)}")
    return text


_BROADCASTS = ("memory", "database")


def _broadcast(value: Any) -> str:
    text = str(value).strip().lower()
    if text not in _BROADCASTS:
        raise _Invalid(f"must be one of {', '.join(_BROADCASTS)}")
    return text


def _same_path(a: str, b: str) -> bool:
    return bool(a and b) and (a.rstrip("/") or "/") == (b.rstrip("/") or "/")


def _log_level(value: Any) -> str:
    text = str(value).strip().lower()
    if text == "warn":
        text = "warning"
    if text not in _LOG_LEVELS:
        raise _Invalid(f"must be one of {', '.join(sorted(_LOG_LEVELS))}")
    return text


@dataclass(frozen=True)
class _Spec:
    convert: Callable[[Any], Any]
    default: Callable[[Mode], Any]
    native: bool = True


_SPECS: dict[str, _Spec] = {
    "host": _Spec(_host, lambda mode: "127.0.0.1"),
    "port": _Spec(_int(0, 65535), lambda mode: 8000),
    "workers": _Spec(_int(1, 1024), lambda mode: os.cpu_count() or 1),
    "debug": _Spec(_bool, lambda mode: mode == "dev"),
    "max_body_size": _Spec(_int(1, 1 << 30), lambda mode: 1024 * 1024),
    "request_timeout": _Spec(_seconds(0.1, 3600), lambda mode: 30.0),
    "shutdown_timeout": _Spec(_seconds(0, 3600), lambda mode: 30.0),
    "header_timeout": _Spec(_seconds(0.1, 300), lambda mode: 5.0),
    "max_connections": _Spec(_int(1, 1_000_000), lambda mode: 10_000),
    "log_level": _Spec(_log_level, lambda mode: "info"),
    "sync_threads": _Spec(_int(1, 1024), lambda mode: 40, native=False),
    "processes": _Spec(_int(1, 64), lambda mode: 1, native=False),
    "database_url": _Spec(_database_url, lambda mode: None, native=False),
    "csrf": _Spec(_bool, lambda mode: True),
    "trusted_origins": _Spec(_strings, lambda mode: ()),
    "trusted_proxies": _Spec(_strings, lambda mode: ()),
    "rate_limit": _Spec(_rate_limit, lambda mode: None),
    "cors": _Spec(_cors, lambda mode: None),
    "hsts": _Spec(_bool, lambda mode: False),
    "hsts_max_age": _Spec(_int(0, 2 * 31_536_000), lambda mode: 31_536_000),
    "hsts_include_subdomains": _Spec(_bool, lambda mode: True),
    "hsts_preload": _Spec(_bool, lambda mode: False),
    "allowed_hosts": _Spec(_strings, lambda mode: ()),
    "https_redirect": _Spec(_bool, lambda mode: False),
    "content_security_policy": _Spec(_optional_text, lambda mode: None),
    "health_path": _Spec(_path, lambda mode: "/healthz"),
    "ready_path": _Spec(_path, lambda mode: "/readyz"),
    "metrics": _Spec(_bool, lambda mode: False),
    "metrics_path": _Spec(_path, lambda mode: "/metrics"),
    "metrics_allow": _Spec(_addresses, lambda mode: ("127.0.0.1", "::1")),
    "log_format": _Spec(_log_format, lambda mode: "text", native=False),
    "shutdown_drain": _Spec(_seconds(0, 3600), lambda mode: 0.0, native=False),
    "tls_cert": _Spec(_optional_text, lambda mode: None),
    "tls_key": _Spec(_optional_text, lambda mode: None),
    "cache": _Spec(_cache, lambda mode: "memory", native=False),
    "broadcast": _Spec(_broadcast, lambda mode: "memory", native=False),
}

SETTING_NAMES: tuple[str, ...] = tuple(_SPECS)


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    workers: int
    debug: bool
    max_body_size: int
    request_timeout: float
    shutdown_timeout: float
    header_timeout: float
    max_connections: int
    log_level: str
    sync_threads: int
    database_url: str | None
    csrf: bool = True
    trusted_origins: tuple[str, ...] = ()
    trusted_proxies: tuple[str, ...] = ()
    rate_limit: str | None = None
    cors: Any = None
    hsts: bool = False
    hsts_max_age: int = 31_536_000
    hsts_include_subdomains: bool = True
    hsts_preload: bool = False
    allowed_hosts: tuple[str, ...] = ()
    https_redirect: bool = False
    content_security_policy: str | None = None
    processes: int = 1
    health_path: str = "/healthz"
    ready_path: str = "/readyz"
    metrics: bool = False
    metrics_path: str = "/metrics"
    metrics_allow: tuple[str, ...] = ("127.0.0.1", "::1")
    log_format: str = "text"
    shutdown_drain: float = 0.0
    tls_cert: str | None = None
    tls_key: str | None = None
    cache: str = "memory"
    broadcast: str = "memory"
    sources: Mapping[str, str] = field(default_factory=dict, compare=False)

    @property
    def scheme(self) -> str:
        """``https`` when the server speaks TLS itself (Feature 014)."""
        return "https" if self.tls_cert and self.tls_key else "http"

    def to_native(self) -> dict[str, Any]:
        """Settings in the shape the native server expects."""
        native: dict[str, Any] = {}
        for name, spec in _SPECS.items():
            if not spec.native:
                continue
            value = getattr(self, name)
            if name == "cors":
                value = None if value is None else value.to_native()
            elif isinstance(value, tuple):
                value = list(value)
            native[name] = value
        return native


def env_name(setting: str) -> str:
    return f"FERRO_{setting.upper()}"


def resolve(
    *,
    cli: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    app: Mapping[str, Any] | None = None,
    mode: Mode = "run",
) -> Settings:
    """Resolves every setting with precedence CLI > environment > App(...) > default."""
    cli = {k: v for k, v in (cli or {}).items() if v is not None}
    app = {k: v for k, v in (app or {}).items() if v is not None}
    env = os.environ if env is None else env
    for name in (*cli, *app):
        if name not in _SPECS:
            raise ConfigurationError(f"unknown setting '{name}'")

    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for name, spec in _SPECS.items():
        if name in cli:
            raw, source = cli[name], "cli"
        elif env_name(name) in env:
            raw, source = env[env_name(name)], f"env ({env_name(name)})"
        elif name in app:
            raw, source = app[name], f"App({name}=...)"
        else:
            raw, source = spec.default(mode), "default"
        try:
            values[name] = spec.convert(raw)
        except _Invalid as problem:
            raise ConfigurationError(
                f"invalid value {raw!r} for setting '{name}' from {source}: {problem}"
            ) from None
        sources[name] = source
    _check_addresses(values, sources)
    _check_tls(values, sources)
    _check_broadcast(values, sources)
    return Settings(**values, sources=sources)


def _check_broadcast(values: Mapping[str, Any], sources: Mapping[str, str]) -> None:
    """Broadcast across processes uses PostgreSQL LISTEN/NOTIFY (Feature 018)."""
    url = values["database_url"] or ""
    if values["broadcast"] == "database" and not url.startswith(("postgresql:", "postgres:")):
        raise ConfigurationError(
            f"setting 'broadcast' is 'database' (from {sources['broadcast']}) but the database "
            "is not PostgreSQL: broadcast across processes uses PostgreSQL LISTEN/NOTIFY; use "
            "'memory' with SQLite"
        )


def _check_tls(values: Mapping[str, Any], sources: Mapping[str, str]) -> None:
    """Certificate and key come together (Feature 014); the files are read at start."""
    for name, other in (("tls_cert", "tls_key"), ("tls_key", "tls_cert")):
        if values[name] is not None and values[other] is None:
            raise ConfigurationError(
                f"setting '{name}' (from {sources[name]}) needs '{other}' too: TLS takes a "
                "certificate file and its private key file"
            )


def _check_addresses(values: Mapping[str, Any], sources: Mapping[str, str]) -> None:
    """Probe and metrics addresses must differ (Feature 011)."""
    clashes = [("ready_path", "health_path")]
    if values["metrics"]:
        if not values["metrics_path"]:
            raise ConfigurationError(
                f"invalid value '' for setting 'metrics_path' from {sources['metrics_path']}: "
                "must not be empty when metrics are on"
            )
        clashes += [("metrics_path", "health_path"), ("metrics_path", "ready_path")]
    for name, other in clashes:
        if _same_path(values[name], values[other]):
            raise ConfigurationError(
                f"invalid value {values[name]!r} for setting '{name}' from {sources[name]}: "
                f"must differ from {other}"
            )
