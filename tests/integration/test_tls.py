"""Feature 014: `ferro run` serving HTTPS and HTTP/2 itself."""

from __future__ import annotations

import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.integration.test_processes import free_port, interrupt

trustme = pytest.importorskip("trustme")
pytest.importorskip("h2")

APP = """import asyncio
import os

from ferro import App

app = App()


@app.get("/info")
async def info(request):
    return {"host": request.headers.get("host"), "pid": os.getpid()}


@app.post("/echo")
async def echo(request):
    return {"body": await request.json()}


@app.get("/slow")
async def slow():
    await asyncio.sleep(0.2)
    return {"ok": True}


@app.get("/events")
async def events():
    from ferro import EventStream

    async def produce():
        for n in range(3):
            yield {"n": n}
            await asyncio.sleep(0.05)

    return EventStream(produce())
"""


@pytest.fixture
def certs(tmp_path: Path) -> tuple[Path, Path, ssl.SSLContext]:
    """A certificate for localhost/127.0.0.1 from a throwaway CA, and a client context trusting
    that CA."""
    ca = trustme.CA()
    issued = ca.issue_cert("localhost", "127.0.0.1")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert.write_bytes(b"".join(blob.bytes() for blob in issued.cert_chain_pems))
    issued.private_key_pem.write_to_path(str(key))
    context = ssl.create_default_context()
    ca.configure_trust(context)
    return cert, key, context


def run(tmp_path: Path, *args: str) -> tuple[subprocess.Popen, int]:
    (tmp_path / "app.py").write_text(APP)
    port = free_port()
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ferro",
            "run",
            "--port",
            str(port),
            "--log-level",
            "warning",
            *list(args),
        ],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=flags,
    )
    return process, port


def wait_ready(process: subprocess.Popen, client: httpx.Client, url: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"server exited: {process.communicate()}")
        try:
            if client.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise AssertionError("server did not start")


def multiplexed(
    port: int, context: ssl.SSLContext, path: str, count: int
) -> tuple[list[int], float]:
    """Opens ``count`` streams at once on one HTTP/2 connection; returns their statuses and the
    time until the last one ended."""
    import h2.config
    import h2.connection
    import h2.events

    context.set_alpn_protocols(["h2"])
    with context.wrap_socket(
        socket.create_connection(("127.0.0.1", port), timeout=30), server_hostname="localhost"
    ) as sock:
        assert sock.selected_alpn_protocol() == "h2"
        conn = h2.connection.H2Connection(h2.config.H2Configuration(client_side=True))
        conn.initiate_connection()
        started = time.monotonic()
        statuses: dict[int, int] = {}
        ended: set[int] = set()
        for _ in range(count):
            stream = conn.get_next_available_stream_id()
            headers = [
                (":method", "GET"),
                (":path", path),
                (":scheme", "https"),
                (":authority", f"localhost:{port}"),
            ]
            conn.send_headers(stream, headers, end_stream=True)
        sock.sendall(conn.data_to_send())
        while len(ended) < count:
            data = sock.recv(65536)
            assert data, "connection closed by the server"
            for event in conn.receive_data(data):
                if isinstance(event, h2.events.ResponseReceived):
                    statuses[event.stream_id] = int(dict(event.headers)[b":status"])
                elif isinstance(event, h2.events.DataReceived):
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                elif isinstance(event, h2.events.StreamEnded):
                    ended.add(event.stream_id)
                elif isinstance(event, (h2.events.StreamReset, h2.events.ConnectionTerminated)):
                    raise AssertionError(f"unexpected {event}")
            sock.sendall(conn.data_to_send())
        return [statuses[s] for s in sorted(statuses)], time.monotonic() - started


def stop(process: subprocess.Popen) -> str:
    interrupt(process)
    _, err = process.communicate(timeout=60)
    assert process.returncode == 0, err
    return err


@pytest.mark.parametrize("processes", ["1", "2"])
def test_https_over_http1_and_http2(tmp_path, certs, processes):
    cert, key, context = certs
    process, port = run(
        tmp_path,
        "--tls-cert",
        str(cert),
        "--tls-key",
        str(key),
        "--processes",
        processes,
        "--max-body-size",
        "4096",
    )
    base = f"https://localhost:{port}"
    try:
        with httpx.Client(verify=context, http2=True, base_url=base) as h2:
            wait_ready(process, h2, "/healthz")
            info = h2.get("/info")
            assert info.http_version == "HTTP/2"
            assert info.json()["host"] == f"localhost:{port}"  # from the authority
            same_origin = h2.post("/echo", json={"a": [1, 2]}, headers={"origin": base})
            assert same_origin.json() == {"body": {"a": [1, 2]}}
            # Over the limit but within the stream's flow-control window: httpx's HTTP/2 client
            # sends the whole body before reading the response.
            assert h2.post("/echo", content=b"x" * 10_000).status_code == 413
            assert h2.get("/missing").status_code == 404
            assert h2.get("/readyz").json() == {"status": "ready"}
            # Server-sent events over HTTP/2 (Feature 018).
            with h2.stream("GET", "/events") as streamed:
                assert streamed.http_version == "HTTP/2"
                lines = [line for line in streamed.iter_lines() if line.startswith("data:")]
            assert lines == ['data: {"n":0}', 'data: {"n":1}', 'data: {"n":2}']
        # SC-002: 50 concurrent requests multiplexed on one connection. (httpx's sync HTTP/2
        # client handles one response at a time, so the streams are driven directly.)
        statuses, elapsed = multiplexed(port, context, "/slow", 50)
        assert statuses == [200] * 50
        assert elapsed < 5, f"50 x 0.2 s took {elapsed:.2f} s: streams were not concurrent"
        with httpx.Client(verify=context, base_url=base) as h1:
            plain = h1.get("/info")
            assert plain.http_version == "HTTP/1.1"
            assert plain.json()["host"] == f"localhost:{port}"
        with pytest.raises(httpx.HTTPError):
            httpx.get(f"http://127.0.0.1:{port}/info", timeout=5)
    finally:
        err = stop(process)
    assert f"https://127.0.0.1:{port}" in err


def test_misconfiguration_stops_before_serving(tmp_path, certs):
    cert, key, _ = certs
    for args, expected in [
        (["--tls-cert", str(cert)], "tls_key"),
        (["--tls-cert", str(tmp_path / "nope.pem"), "--tls-key", str(key)], "nope.pem"),
        (["--tls-cert", str(cert), "--tls-key", str(cert)], "no private key"),
        (
            ["--tls-cert", str(cert), "--tls-key", str(tmp_path / "nope.pem"), "--processes", "2"],
            "nope.pem",
        ),
    ]:
        process, _ = run(tmp_path, *args)
        _, err = process.communicate(timeout=60)
        assert process.returncode != 0, err
        assert expected in err, err
        assert "Traceback" not in err, err
        assert "BEGIN" not in err, err
