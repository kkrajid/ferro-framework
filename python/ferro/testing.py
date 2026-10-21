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
import base64
import contextlib
import http.client
import json as jsonlib
import os
import socket
import struct
from collections.abc import Iterable, Iterator
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlencode

from ferro.app import App
from ferro.response import ServerSentEvent
from ferro.websockets import WebSocketDisconnect

__all__ = [
    "TestClient",
    "TestResponse",
    "TestStreamResponse",
    "TestWebSocket",
    "WebSocketDisconnect",
    "WebSocketRejected",
    "connect_websocket",
]


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
        content: bytes | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> TestResponse:
        """``json=`` sends JSON, ``data=`` a form, ``content=`` raw bytes (or text as UTF-8)
        with the ``content-type`` you give in ``headers``."""
        if self._address is None:
            raise RuntimeError("use TestClient in a `with` block")
        body: bytes | None = None
        sent = {k.lower(): v for k, v in (headers or {}).items()}
        if content is not None:
            body = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        elif json is not None:
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

    # -- WebSockets and streams (Feature 018) ---------------------------------------------------

    def _headers_with_cookies(self, headers: dict[str, str] | None) -> dict[str, str]:
        sent = {k.lower(): v for k, v in (headers or {}).items()}
        if self.cookies and "cookie" not in sent:
            sent["cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        return sent

    def websocket_connect(
        self, path: str, *, headers: dict[str, str] | None = None
    ) -> TestWebSocket:
        """Opens a WebSocket to ``path`` (use it as a context manager), with this client's
        cookies. Raises :class:`WebSocketRejected` when the server answers the handshake with an
        HTTP response instead (403, 426, 429, ...)."""
        if self._address is None:
            raise RuntimeError("use TestClient in a `with` block")
        return connect_websocket(self._address, path, headers=self._headers_with_cookies(headers))

    @contextlib.contextmanager
    def stream(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Iterator[TestStreamResponse]:
        """Sends a request and gives the response before its body is complete (streaming
        responses, server-sent events)::

            with client.stream("GET", "/events") as response:
                for event in response.iter_events():
                    ...
        """
        if self._address is None:
            raise RuntimeError("use TestClient in a `with` block")
        body: bytes | None = None
        sent = self._headers_with_cookies(headers)
        if json is not None:
            body = jsonlib.dumps(json).encode()
            sent.setdefault("content-type", "application/json")
        elif data is not None:
            body = urlencode(data, doseq=True).encode()
            sent.setdefault("content-type", "application/x-www-form-urlencoded")
        connection = http.client.HTTPConnection(*self._address, timeout=30)
        try:
            connection.request(method.upper(), path, body=body, headers=sent)
            yield TestStreamResponse(connection.getresponse())
        finally:
            connection.close()


def connect_websocket(
    address: tuple[str, int], path: str, *, headers: dict[str, str] | None = None
) -> TestWebSocket:
    """Opens a WebSocket to any running server (``("127.0.0.1", 8000)``, ``"/ws"``); raises
    :class:`WebSocketRejected` when the handshake is refused."""
    sock = socket.create_connection(address, timeout=30)
    key = base64.b64encode(os.urandom(16)).decode()
    extra = dict(headers or {})
    # A Host given by the caller replaces the address (one Host header per request).
    host = next((extra.pop(k) for k in list(extra) if k.lower() == "host"), None)
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host or f'{address[0]}:{address[1]}'}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    lines += [f"{k}: {v}" for k, v in extra.items()]
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
    reader = sock.makefile("rb")
    status_line = reader.readline().decode("latin-1")
    raw_headers: list[tuple[str, str]] = []
    while True:
        line = reader.readline().decode("latin-1").rstrip("\r\n")
        if not line:
            break
        name, _, value = line.partition(":")
        raw_headers.append((name.strip(), value.strip()))
    status = int(status_line.split(" ")[1])
    if status != 101:
        length = next((int(v) for n, v in raw_headers if n.lower() == "content-length"), None)
        body = reader.read(length) if length is not None else b""
        sock.close()
        raise WebSocketRejected(TestResponse(status, raw_headers, body))
    return TestWebSocket(sock, reader)


class WebSocketRejected(Exception):
    """The server refused a WebSocket handshake; ``response`` is its HTTP answer."""

    __test__ = False

    def __init__(self, response: TestResponse) -> None:
        super().__init__(f"WebSocket handshake refused with {response.status_code}")
        self.response = response


class TestWebSocket:
    """The client side of a WebSocket opened by :meth:`TestClient.websocket_connect`."""

    __test__ = False

    def __init__(self, sock: socket.socket, reader: Any) -> None:
        self._sock = sock
        self._reader = reader
        self.close_code: int | None = None
        self.close_reason: str | None = None

    def __enter__(self) -> TestWebSocket:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def _read_frame(self) -> tuple[int, bytes]:
        head = self._reader.read(2)
        if len(head) < 2:
            raise WebSocketDisconnect(1006, "connection lost")
        opcode = head[0] & 0x0F
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._reader.read(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._reader.read(8))[0]
        return opcode, self._reader.read(length)

    def send(self, data: str | bytes) -> None:
        if isinstance(data, str):
            self._send_frame(0x1, data.encode("utf-8"))
        else:
            self._send_frame(0x2, bytes(data))

    def send_text(self, text: str) -> None:
        self.send(text)

    def send_bytes(self, data: bytes) -> None:
        self.send(data)

    def send_json(self, value: Any) -> None:
        self.send(jsonlib.dumps(value))

    def receive(self, timeout: float = 10) -> str | bytes:
        """The next message; raises ``WebSocketDisconnect`` when the server closes."""
        if self.close_code is not None:
            raise WebSocketDisconnect(self.close_code, self.close_reason or "")
        self._sock.settimeout(timeout)
        while True:
            opcode, payload = self._read_frame()
            if opcode == 0x1:
                return payload.decode("utf-8")
            if opcode == 0x2:
                return payload
            if opcode == 0x9:
                self._send_frame(0xA, payload)
            elif opcode == 0x8:
                code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else 1005
                self.close_code, self.close_reason = code, payload[2:].decode("utf-8", "replace")
                with contextlib.suppress(OSError):
                    self._send_frame(0x8, payload[:2])
                raise WebSocketDisconnect(code, self.close_reason)

    def receive_text(self, timeout: float = 10) -> str:
        message = self.receive(timeout)
        assert isinstance(message, str), f"expected text, got {message!r}"
        return message

    def receive_bytes(self, timeout: float = 10) -> bytes:
        message = self.receive(timeout)
        assert isinstance(message, bytes), f"expected bytes, got {message!r}"
        return message

    def receive_json(self, timeout: float = 10) -> Any:
        return jsonlib.loads(self.receive(timeout))

    def close(self, code: int = 1000, reason: str = "") -> None:
        """Sends a close frame and waits briefly for the server's answer."""
        if self.close_code is None:
            with contextlib.suppress(OSError):
                self._send_frame(0x8, struct.pack(">H", code) + reason.encode("utf-8"))
                self._sock.settimeout(5)
                while True:
                    opcode, payload = self._read_frame()
                    if opcode == 0x8:
                        self.close_code = (
                            struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else 1005
                        )
                        break
            if self.close_code is None:
                self.close_code = code
        with contextlib.suppress(OSError):
            self._sock.close()


class TestStreamResponse:
    """A response read while it arrives (:meth:`TestClient.stream`)."""

    __test__ = False

    def __init__(self, raw: http.client.HTTPResponse) -> None:
        self._raw = raw
        self.status_code = raw.status
        merged = _Headers()
        for name, value in raw.getheaders():
            key = name.lower()
            merged[key] = f"{merged[key]}, {value}" if key in merged else value
        self.headers = merged

    def iter_bytes(self) -> Iterator[bytes]:
        while True:
            chunk = self._raw.read1(65536)
            if not chunk:
                return
            yield chunk

    def iter_lines(self) -> Iterator[str]:
        while True:
            line = self._raw.readline()
            if not line:
                return
            yield line.decode("utf-8").rstrip("\r\n")

    def iter_events(self) -> Iterator[ServerSentEvent]:
        """Server-sent events as they arrive (comments are skipped)."""
        fields: dict[str, Any] = {}
        data: list[str] = []
        for line in self.iter_lines():
            if not line:
                if data or fields:
                    yield ServerSentEvent(
                        "\n".join(data) if data else None,
                        event=fields.get("event"),
                        id=fields.get("id"),
                        retry=int(fields["retry"]) if "retry" in fields else None,
                    )
                fields, data = {}, []
                continue
            if line.startswith(":"):
                continue
            name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if name == "data":
                data.append(value)
            else:
                fields[name] = value
