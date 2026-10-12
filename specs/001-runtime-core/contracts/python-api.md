# Contract: Public Python API (Feature 001)

Everything below is importable from `ferro`. Anything not listed is private and may change.

```python
from ferro import App, Request, Response, JSONResponse, HTTPException, ValidationError
from ferro import ConfigurationError, __version__
```

## App

```python
class App:
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
    ) -> None: ...

    def get(self, path: str, *, name: str | None = None) -> Callable[[H], H]: ...
    def post(self, path: str, *, name: str | None = None) -> Callable[[H], H]: ...
    def put(self, path: str, *, name: str | None = None) -> Callable[[H], H]: ...
    def patch(self, path: str, *, name: str | None = None) -> Callable[[H], H]: ...
    def delete(self, path: str, *, name: str | None = None) -> Callable[[H], H]: ...
    def route(self, path: str, methods: Iterable[str], *, name: str | None = None) -> Callable[[H], H]: ...

    @property
    def routes(self) -> list[RouteInfo]: ...   # (methods, path, name) in registration order

    def run(self, *, host: str | None = None, port: int | None = None,
            workers: int | None = None, debug: bool | None = None) -> None: ...
```

- Decorators return the original handler unchanged (it stays directly callable in tests).
- Path syntax and handler signatures are validated at decoration time → `ConfigurationError`.
- Duplicates are detected when the route table is built (server start or `ferro routes`).
- `App.run()` is equivalent to `ferro run` for the same app (blocking; handles Ctrl+C).

## Handler parameters

| Parameter | Receives |
|---|---|
| named `request` (or annotated `Request`) | the `Request` |
| name equals a path parameter | the path value, converted per annotation |
| any other name | query parameter of that name, converted per annotation; default used if absent |

Supported annotations: none, `str`, `int`, `float`, `bool`, `X | None` / `Optional[X]` of those,
`Request`. Conversion failure or missing required value → `ValidationError` (422).

## Request

```python
class Request:
    method: str
    path: str
    raw_path: str
    query_string: str
    query: QueryParams           # .get(k, default=None) .getlist(k) .items() .keys() [k] in len
    path_params: dict[str, str]
    headers: Headers             # case-insensitive: .get(k, default=None) .getlist(k) .items() [k] in
    client: tuple[str, int] | None
    request_id: str
    async def body(self) -> bytes: ...
    async def json(self) -> Any: ...   # raises HTTPException(400) on invalid JSON / content type
```

## Response / JSONResponse

```python
class Response:
    def __init__(self, content: Any = None, status: int = 200,
                 headers: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
                 media_type: str | None = None) -> None: ...
    content: Any
    status: int
    headers: list[tuple[str, str]]
    media_type: str | None

class JSONResponse(Response):   # always JSON-encodes content; media_type application/json
    ...
```

## Exceptions

```python
class HTTPException(Exception):
    def __init__(self, status: int, message: str | None = None,
                 headers: Mapping[str, str] | None = None) -> None: ...
    status: int
    message: str            # defaults to the standard reason phrase
    headers: dict[str, str]

class ValidationError(HTTPException):     # status 422
    details: list[dict[str, str]]          # {"param", "location", "problem"}

class ConfigurationError(Exception): ...  # invalid routes, settings, app target
```
