"""``TestClient``: run an application in-process and talk to it over HTTP in tests.

```python
from ferro.testing import TestClient
from app import app

def test_home():
    with TestClient(app, database_url="sqlite:///:memory:") as client:
        assert client.get("/").status_code == 200
```

Standard library only (no extra test dependencies).
"""

from __future__ import annotations

import asyncio
import http.client
import json as jsonlib
from collections.abc import Iterable
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlencode

from ferro.app import App

__all__ = ["TestClient", "TestResponse"]


class _Headers(dict):
    """Response headers with case-insensitive lookup (repeated headers joined by ``, ``)."""

    def __getitem__(self, key: str) -> str:
        return super().__getitem__(key.lower())

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(key.lower(), default)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and super().__contains__(key.lower())


class TestResponse:
    __test__ = False  # not a pytest test class

    def __init__(self, status: int, headers: list[tuple[str, str]], content: bytes) -> None:
        self.status_code = status
        merged = _Headers()
        for name, value in headers:
            key = name.lower()
            merged[key] = f"{merged[key]}, {value}" if key in merged else value
        self.headers = merged
        self.raw_headers = headers
        self.content = content

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return jsonlib.loads(self.content)

    def __repr__(self) -> str:
        return f"<TestResponse {self.status_code}>"


class TestClient:
    """Serves ``app`` on a free local port for the duration of a ``with`` block.

    With a ``database_url``, tables are created for every registered model
    (``create_tables=True``), for the listed models only, or not at all (``False``).
    """

    __test__ = False

    def __init__(
        self,
        app: App,
        *,
        database_url: str | None = None,
        create_tables: bool | Iterable[type] = True,
        debug: bool = True,
    ) -> None:
        self.app = app
        self.database_url = database_url
        self.create_tables = create_tables
        self.debug = debug
        self.cookies: dict[str, str] = {}
        self._runner: Any = None
        self._address: tuple[str, int] | None = None

    def __enter__(self) -> TestClient:
        from ferro import db
        from ferro.config import resolve
        from ferro.server import ServerRunner

        cli = {"host": "127.0.0.1", "port": 0, "workers": 1, "debug": self.debug}
        if self.database_url:
            cli["database_url"] = self.database_url
        settings = resolve(cli=cli, app=self.app._settings, mode="run")
        self._runner = ServerRunner(self.app, settings)
        self._address = self._runner.start()
        if settings.database_url and self.create_tables:
            models = None if self.create_tables is True else list(self.create_tables)
            asyncio.run(db.create_tables(models))
        return self

    def __exit__(self, *exc: Any) -> None:
        runner, self._runner = self._runner, None
        if runner is not None:
            runner.stop(graceful=True)
            runner.wait(10)
            runner.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> TestResponse:
        if self._address is None:
            raise RuntimeError("use TestClient in a `with` block")
        body: bytes | None = None
        sent = {k.lower(): v for k, v in (headers or {}).items()}
        if json is not None:
            body = jsonlib.dumps(json).encode()
            sent.setdefault("content-type", "application/json")
        elif data is not None:
            body = urlencode(data, doseq=True).encode()
            sent.setdefault("content-type", "application/x-www-form-urlencoded")
        if self.cookies and "cookie" not in sent:
            sent["cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        connection = http.client.HTTPConnection(*self._address, timeout=30)
        try:
            connection.request(method.upper(), path, body=body, headers=sent)
            raw = connection.getresponse()
            response = TestResponse(raw.status, raw.getheaders(), raw.read())
        finally:
            connection.close()
        for name, value in response.raw_headers:
            if name.lower() == "set-cookie":
                cookie = SimpleCookie()
                cookie.load(value)
                for key, morsel in cookie.items():
                    if morsel["max-age"] == "0" or morsel.value == "":
                        self.cookies.pop(key, None)
                    else:
                        self.cookies[key] = morsel.value
        return response

    def get(self, path: str, **kwargs: Any) -> TestResponse:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> TestResponse:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> TestResponse:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> TestResponse:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> TestResponse:
        return self.request("DELETE", path, **kwargs)

    def options(self, path: str, **kwargs: Any) -> TestResponse:
        return self.request("OPTIONS", path, **kwargs)
