"""User Story 5 — `ferro dev` auto-reload (FR-021, SC-005)."""

import time

import httpx

APP = """from ferro import App
app = App()

@app.get("/")
async def home():
    return {{"version": {version}}}
"""


def wait_for_version(base_url: str, version: int, timeout: float) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        try:
            if httpx.get(f"{base_url}/", timeout=1).json() == {"version": version}:
                return time.monotonic() - started
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(0.05)
    raise AssertionError(f"version {version} not served within {timeout}s")


def test_edit_is_served_after_reload(ferro_cli, tmp_path):
    source = tmp_path / "app.py"
    source.write_text(APP.format(version=1))
    port = _free_port()
    server = ferro_cli("dev", "--port", str(port), cwd=tmp_path)
    base_url = f"http://127.0.0.1:{port}"
    wait_for_version(base_url, 1, 10)

    time.sleep(0.6)  # distinct mtime
    source.write_text(APP.format(version=2))
    elapsed = wait_for_version(base_url, 2, 10)
    assert elapsed < 4, f"reload took {elapsed:.2f}s"

    server.interrupt()
    assert server.finish() == 0


def test_syntax_error_is_reported_and_recovered(ferro_cli, tmp_path):
    source = tmp_path / "app.py"
    source.write_text(APP.format(version=1))
    port = _free_port()
    server = ferro_cli("dev", "--port", str(port), cwd=tmp_path)
    base_url = f"http://127.0.0.1:{port}"
    wait_for_version(base_url, 1, 10)

    time.sleep(0.6)
    source.write_text("from ferro import App\napp = App(\n")
    server.wait_for(r"SyntaxError", timeout=10)
    server.wait_for(r"waiting for changes", timeout=10)

    time.sleep(0.6)
    source.write_text(APP.format(version=3))
    wait_for_version(base_url, 3, 10)

    server.interrupt()
    assert server.finish() == 0


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
