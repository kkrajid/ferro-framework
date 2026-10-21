"""Security configuration: CORS and cookie defaults (Feature 006); security scheme
dependencies for APIs (Feature 026).

The checks themselves run in the native engine before a request reaches Python: the
cross-origin (CSRF) check, CORS preflights, rate limiting and security headers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ferro.exceptions import ConfigurationError

__all__ = [
    "CORS",
    "APIKeyCookie",
    "APIKeyHeader",
    "APIKeyQuery",
    "HTTPBasic",
    "HTTPBearer",
    "SecurityScheme",
]


def _strings(value: str | Sequence[str], what: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    items = tuple(value)
    for item in items:
        if not isinstance(item, str):
            raise ConfigurationError(f"CORS {what} must be strings, got {item!r}")
    return items


@dataclass(frozen=True)
class CORS:
    """Which other sites may call the API from a browser::

        App(cors=CORS(allow_origins=["https://app.example.com"], allow_credentials=True))

    ``allow_origins=["*"]`` allows every site but cannot be combined with credentials.
    Preflight requests are answered by the engine; allowed origins may also send unsafe
    requests (they pass the cross-origin check).
    """

    allow_origins: Sequence[str] = ()
    allow_methods: Sequence[str] = ("GET", "POST", "PUT", "PATCH", "DELETE")
    allow_headers: Sequence[str] = ()
    expose_headers: Sequence[str] = ()
    allow_credentials: bool = False
    max_age: int = 600

    def __post_init__(self) -> None:
        for name in ("allow_origins", "allow_methods", "allow_headers", "expose_headers"):
            object.__setattr__(self, name, _strings(getattr(self, name), name))
        if "*" in self.allow_origins and self.allow_credentials:
            raise ConfigurationError(
                "CORS allow_origins=['*'] cannot be combined with allow_credentials=True; list "
                "the allowed origins"
            )
        if isinstance(self.max_age, bool) or not isinstance(self.max_age, int):
            raise ConfigurationError("CORS max_age must be a number of seconds")
        if not 0 <= self.max_age <= 86_400:
            raise ConfigurationError("CORS max_age must be between 0 and 86400 seconds")

    def to_native(self) -> dict[str, Any]:
        return {
            "allow_origins": list(self.allow_origins),
            "allow_methods": list(self.allow_methods),
            "allow_headers": list(self.allow_headers),
            "expose_headers": list(self.expose_headers),
            "allow_credentials": self.allow_credentials,
            "max_age": self.max_age,
        }


# Cookies are `Secure` unless the running server is in development mode (set at server start).
_secure_cookies = True


def set_secure_cookie_default(secure: bool) -> None:
    global _secure_cookies
    _secure_cookies = secure


def secure_cookie_default() -> bool:
    return _secure_cookies


class SecurityScheme:
    """A dependency reading credentials from the request and describing them in the OpenAPI
    document (Feature 026). ``Depends(scheme)`` gives the credentials; a missing credential is a
    ``401`` unless ``auto_error=False``, which gives ``None`` instead. Checking the credential
    (looking the token up) is the job of the dependency that uses it::

        bearer = HTTPBearer()

        async def current_user(token: str = Depends(bearer)) -> User: ...
    """

    #: Name under ``components.securitySchemes``.
    scheme_name: str

    def __init__(
        self,
        *,
        scheme_name: str | None = None,
        description: str | None = None,
        auto_error: bool = True,
    ) -> None:
        self.scheme_name = scheme_name or type(self).__name__
        self.description = description
        self.auto_error = auto_error

    def openapi(self) -> dict[str, Any]:
        """The scheme's OpenAPI ``securitySchemes`` entry."""
        raise NotImplementedError

    def _scheme(self, entry: dict[str, Any]) -> dict[str, Any]:
        if self.description:
            entry["description"] = self.description
        return entry

    def _missing(self, message: str) -> None:
        if self.auto_error:
            from ferro.exceptions import HTTPException

            raise HTTPException(401, message, headers=self._challenge())
        return None

    def _challenge(self) -> dict[str, str]:
        return {}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(scheme_name={self.scheme_name!r})"


class HTTPBearer(SecurityScheme):
    """``Authorization: Bearer <token>``; gives the token."""

    def __init__(self, *, bearer_format: str | None = None, **options: Any) -> None:
        super().__init__(**options)
        self.bearer_format = bearer_format

    def openapi(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"type": "http", "scheme": "bearer"}
        if self.bearer_format:
            entry["bearerFormat"] = self.bearer_format
        return self._scheme(entry)

    def _challenge(self) -> dict[str, str]:
        return {"WWW-Authenticate": "Bearer"}

    async def __call__(self, request) -> str | None:
        scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            return self._missing("Missing bearer token")
        return token


class HTTPBasic(SecurityScheme):
    """``Authorization: Basic <base64>``; gives ``(username, password)``. Send it over HTTPS
    only."""

    def __init__(self, *, realm: str | None = None, **options: Any) -> None:
        super().__init__(**options)
        self.realm = realm

    def openapi(self) -> dict[str, Any]:
        return self._scheme({"type": "http", "scheme": "basic"})

    def _challenge(self) -> dict[str, str]:
        realm = f' realm="{self.realm}"' if self.realm else ""
        return {"WWW-Authenticate": f"Basic{realm}"}

    async def __call__(self, request) -> tuple[str, str] | None:
        import base64
        import binascii

        scheme, _, encoded = (request.headers.get("authorization") or "").partition(" ")
        if scheme.lower() != "basic" or not encoded.strip():
            return self._missing("Missing basic credentials")
        try:
            decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return self._missing("Malformed basic credentials")
        username, colon, password = decoded.partition(":")
        if not colon:
            return self._missing("Malformed basic credentials")
        return username, password


class _APIKey(SecurityScheme):
    location = ""

    def __init__(self, *, name: str, **options: Any) -> None:
        super().__init__(**options)
        self.name = name

    def openapi(self) -> dict[str, Any]:
        return self._scheme({"type": "apiKey", "in": self.location, "name": self.name})

    def _read(self, request: Any) -> str | None:
        raise NotImplementedError

    async def __call__(self, request) -> str | None:
        value = self._read(request)
        if not value:
            return self._missing(f"Missing API key ({self.location} {self.name!r})")
        return value


class APIKeyHeader(_APIKey):
    """An API key in the header ``name``."""

    location = "header"

    def _read(self, request: Any) -> str | None:
        return request.headers.get(self.name)


class APIKeyQuery(_APIKey):
    """An API key in the query parameter ``name`` (it ends up in logs: prefer a header)."""

    location = "query"

    def _read(self, request: Any) -> str | None:
        return request.query.get(self.name)


class APIKeyCookie(_APIKey):
    """An API key in the cookie ``name``."""

    location = "cookie"

    def _read(self, request: Any) -> str | None:
        return request.cookies.get(self.name)


#: The scheme of routes guarded by ``login_required``, ``staff_required`` and
#: ``permission_required``: the session cookie of ``ferro.auth``.
SESSION_COOKIE = APIKeyCookie(name="sessionid", scheme_name="session")
