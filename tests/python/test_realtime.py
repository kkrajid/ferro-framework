"""Feature 018: WebSocket endpoints, streaming responses and server-sent events (through the real
server, with TestClient)."""

from __future__ import annotations

import asyncio
import http.client
import threading
import time

import pytest

from ferro import (
    App,
    EventStream,
    HTTPException,
    ServerSentEvent,
    StreamingResponse,
    WebSocket,
    WebSocketDisconnect,
    auth,
)
from ferro.auth import Group, Permission, Session, User
from ferro.testing import TestClient, WebSocketRejected


class Record:
    """Values written by handlers on the server's event loop, read by the test thread."""

    def __init__(self) -> None:
        self.items: list = []
        self.changed = threading.Condition()

    def add(self, item) -> None:
        with self.changed:
            self.items.append(item)
            self.changed.notify_all()

    def wait_for(self, item, timeout: float = 5) -> float:
        started = time.monotonic()
        with self.changed:
            if not self.changed.wait_for(lambda: item in self.items, timeout):
                raise AssertionError(f"{item!r} not recorded; got {self.items!r}")
        return time.monotonic() - started


def socket_app(record: Record) -> App:
    app = App(max_body_size=4096)

    @app.websocket("/echo")
    async def echo(ws):
        async for message in ws:
            await ws.send(message)
        record.add(("echo ended", ws.close_code))

    @app.websocket("/json")
    async def json_echo(ws: WebSocket):
        while True:
            value = await ws.receive_json()
            await ws.send_json({"got": value})

    @app.websocket("/rooms/{room}")
    async def room(ws, room: int, name: str = "anon"):
        await ws.send_json({"room": room, "name": name, "path": ws.path})

    @app.websocket("/sleepy")
    async def sleepy(ws):
        await ws.send("ready")
        try:
            await asyncio.sleep(30)
        finally:
            record.add("sleepy cancelled")

    @app.websocket("/broken")
    async def broken(ws):
        await ws.receive()
        raise RuntimeError("boom")

    @app.websocket("/goodbye")
    async def goodbye(ws):
        await ws.close(4001, "bye")
        try:
            await ws.send("too late")
        except WebSocketDisconnect:
            record.add("send after close refused")

    @app.websocket("/done")
    async def done(ws):
        await ws.send("only one")

    return app


def test_echo_text_binary_and_json():
    record = Record()
    with TestClient(socket_app(record)) as client:
        with client.websocket_connect("/echo") as ws:
            for n in range(1000):
                ws.send(f"message {n}")
            assert [ws.receive() for _ in range(1000)] == [f"message {n}" for n in range(1000)]
            ws.send(b"\x00\x01\xff")
            assert ws.receive() == b"\x00\x01\xff"
        record.wait_for(("echo ended", 1000))
        with client.websocket_connect("/json") as ws:
            ws.send_json({"a": [1, 2]})
            assert ws.receive_json() == {"got": {"a": [1, 2]}}


def test_path_and_query_parameters():
    with TestClient(socket_app(Record())) as client:
        with client.websocket_connect("/rooms/7?name=ann") as ws:
            assert ws.receive_json() == {"room": 7, "name": "ann", "path": "/rooms/7"}
        with client.websocket_connect("/rooms/seven") as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive()
            assert closed.value.code == 1008
            assert "room" in closed.value.reason


def test_client_leaving_cancels_a_busy_handler():
    record = Record()
    with TestClient(socket_app(record)) as client:
        ws = client.websocket_connect("/sleepy")
        assert ws.receive() == "ready"
        ws.close()
        assert record.wait_for("sleepy cancelled") < 1.0  # SC-003


def test_close_codes():
    record = Record()
    with TestClient(socket_app(record)) as client:
        with client.websocket_connect("/broken") as ws:
            ws.send("go")
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive()
            assert closed.value.code == 1011
        with client.websocket_connect("/goodbye") as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive()
            assert (closed.value.code, closed.value.reason) == (4001, "bye")
        record.wait_for("send after close refused")
        with client.websocket_connect("/done") as ws:
            assert ws.receive() == "only one"
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive()
            assert closed.value.code == 1000
        with client.websocket_connect("/echo") as ws:
            ws.send("x" * 5000)
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive()
            assert closed.value.code == 1009


def test_handshake_checks():
    with TestClient(socket_app(Record())) as client:
        with pytest.raises(WebSocketRejected) as refused:
            client.websocket_connect(
                "/echo",
                headers={"origin": "https://evil.example", "sec-fetch-site": "cross-site"},
            )
        assert refused.value.response.status_code == 403
        plain = client.get("/echo")
        assert plain.status_code == 426
        assert plain.headers["upgrade"] == "websocket"


def test_sockets_see_the_logged_in_user():
    app = App()
    app.add_auth()

    @app.post("/login")
    async def sign_in(request):
        data = await request.json()
        user = await auth.authenticate(data["username"], data["password"])
        if user is None:
            raise HTTPException(401)
        await auth.login(request, user)

    @app.post("/setup")
    async def setup():
        await User.objects.create_user("ann", password="correct horse")

    @app.websocket("/whoami")
    async def whoami(ws):
        await ws.send_json({"authenticated": ws.user.is_authenticated, "name": ws.user.username})

    tables = [User, Group, Permission, Session]
    with TestClient(app, database_url="sqlite:///:memory:", create_tables=tables) as client:
        client.post("/setup")
        with client.websocket_connect("/whoami") as ws:
            assert ws.receive_json() == {"authenticated": False, "name": ""}
        login = client.post("/login", json={"username": "ann", "password": "correct horse"})
        assert login.status_code == 204
        with client.websocket_connect("/whoami") as ws:
            assert ws.receive_json() == {"authenticated": True, "name": "ann"}


def test_sockets_drop_sessions_of_changed_passwords():
    """After a password change the session is over for sockets too: no user and none of its
    data (security review, 2026-10-20)."""
    app = App()
    app.add_auth()

    @app.post("/login")
    async def sign_in(request):
        user = await auth.authenticate("ann", "correct horse")
        await auth.login(request, user)
        request.session["tenant"] = "acme"

    @app.post("/setup")
    async def setup():
        await User.objects.create_user("ann", password="correct horse")

    @app.post("/change")
    async def change():
        user = await User.objects.get(username="ann")
        await user.set_password("battery staple horse")
        await user.save()

    @app.websocket("/whoami")
    async def whoami(ws):
        await ws.send_json({"name": ws.user.username, "session": dict(ws.session)})

    tables = [User, Group, Permission, Session]
    with TestClient(app, database_url="sqlite:///:memory:", create_tables=tables) as client:
        client.post("/setup")
        client.post("/login")
        with client.websocket_connect("/whoami") as ws:
            assert ws.receive_json()["session"]["tenant"] == "acme"
        client.post("/change")
        with client.websocket_connect("/whoami") as ws:
            assert ws.receive_json() == {"name": "", "session": {}}


# -- streaming and server-sent events -----------------------------------------------------------


def stream_app(record: Record) -> App:
    app = App()

    @app.middleware
    async def tag(request, call_next):
        response = await call_next(request)
        response.headers.append(("x-tagged", "yes"))
        return response

    @app.get("/count")
    async def count():
        async def numbers():
            for n in range(5):
                await asyncio.sleep(0.1)
                yield f"{n}\n"

        return StreamingResponse(numbers(), media_type="text/plain")

    @app.get("/list")
    async def from_list():
        return StreamingResponse(["a", b"b", "c"], media_type="text/plain")

    @app.get("/events")
    async def events():
        async def produce():
            yield "hello"
            yield ServerSentEvent({"n": 1}, event="tick", id=1)
            yield ServerSentEvent("two\nlines", id=2, retry=1000)

        return EventStream(produce())

    @app.get("/quiet")
    async def quiet():
        async def produce():
            await asyncio.sleep(0.35)
            yield "late"

        return EventStream(produce(), ping=0.1)

    @app.get("/endless")
    async def endless():
        async def produce():
            try:
                n = 0
                while True:
                    yield ServerSentEvent(n)
                    n += 1
                    await asyncio.sleep(0.05)
            finally:
                record.add("generator closed")

        return EventStream(produce())

    @app.get("/failing")
    async def failing():
        async def produce():
            yield "first"
            raise RuntimeError("stream broke")

        return StreamingResponse(produce())

    return app


def test_streaming_body_arrives_as_produced():
    with TestClient(stream_app(Record())) as client:
        with client.stream("GET", "/count") as response:
            assert response.status_code == 200
            assert response.headers["x-tagged"] == "yes"
            arrivals = []
            for line in response.iter_lines():
                arrivals.append((time.monotonic(), line))
        assert [line for _, line in arrivals] == ["0", "1", "2", "3", "4"]
        assert arrivals[-1][0] - arrivals[0][0] >= 0.3
        assert client.get("/list").text == "abc"


def test_server_sent_events():
    with TestClient(stream_app(Record())) as client:
        with client.stream("GET", "/events") as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-cache"
            events = list(response.iter_events())
        assert [(e.data, e.event, e.id) for e in events] == [
            ("hello", None, None),
            ('{"n":1}', "tick", "1"),
            ("two\nlines", None, "2"),
        ]
        assert events[2].retry == 1000
        with client.stream("GET", "/quiet") as response:
            lines = list(response.iter_lines())
        assert lines.count(": ping") >= 2
        assert "data: late" in lines


def test_event_fields_are_checked_when_sent():
    event = ServerSentEvent("ok", event="tick")
    event.event = "tick\ndata: injected"
    with pytest.raises(ValueError, match="line breaks"):
        event.encode()


def test_disconnect_closes_the_generator():
    record = Record()
    with TestClient(stream_app(record)) as client:
        with client.stream("GET", "/endless") as response:
            events = response.iter_events()
            assert next(events).data == "0"
            assert next(events).data == "1"
        assert record.wait_for("generator closed") < 1.0  # SC-003


def test_disconnect_while_the_generator_waits(capfd):
    """A generator suspended in an `await` (not at a `yield`) when the client leaves is cancelled
    and closed cleanly: its `finally` runs and nothing is logged as failed."""
    record = Record()
    app = App()

    def waiting(label):
        async def produce():
            try:
                yield "first"
                await asyncio.sleep(30)
                yield "never"
            finally:
                record.add(f"{label} closed")

        return produce()

    @app.get("/stream")
    async def stream():
        return StreamingResponse(waiting("stream"))

    @app.get("/events")
    async def events():
        return EventStream(waiting("events"), ping=None)

    with TestClient(app) as client:
        for path in ("/stream", "/events"):
            with client.stream("GET", path) as response:
                assert b"first" in next(response.iter_bytes())
        assert record.wait_for("stream closed") < 1.0
        assert record.wait_for("events closed") < 1.0
    log = capfd.readouterr().err
    assert "already running" not in log
    assert "stream cleanup failed" not in log and "stream failed" not in log


def test_a_failing_stream_is_cut_off():
    """The client gets what was produced, then an incomplete body — not a complete-looking one."""
    with TestClient(stream_app(Record())) as client:
        with client.stream("GET", "/failing") as response:
            received = []
            with pytest.raises(http.client.IncompleteRead):
                for chunk in response.iter_bytes():
                    received.append(chunk)
        assert b"".join(received) == b"first"
        assert client.get("/list").text == "abc"  # the server carries on


def test_events_arrive_as_soon_as_they_are_yielded():
    """SC-002: each event reaches the client within 100 ms of being yielded."""
    app = App()

    @app.get("/clock")
    async def clock():
        async def ticks():
            for _ in range(3):
                await asyncio.sleep(0.2)
                yield ServerSentEvent(repr(time.time()))

        return EventStream(ticks())

    with TestClient(app) as client, client.stream("GET", "/clock") as response:
        delays = [time.time() - float(event.data) for event in response.iter_events()]
    assert len(delays) == 3
    assert max(delays) < 0.1, delays


def test_open_sockets_and_streams_are_measured():
    app = App(metrics=True)

    @app.websocket("/hold")
    async def hold(ws):
        await ws.send("held")
        await ws.receive()

    with TestClient(app) as client:
        with client.websocket_connect("/hold") as ws:
            assert ws.receive() == "held"
            text = client.get("/metrics").text
            assert "ferro_websocket_connections 1" in text
            assert "ferro_http_streams_open 0" in text
        deadline = time.monotonic() + 5
        while "ferro_websocket_connections 0" not in client.get("/metrics").text:
            assert time.monotonic() < deadline
            time.sleep(0.05)
