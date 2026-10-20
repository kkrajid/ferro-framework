"""User Story 5 — development and production commands (FR-020-FR-026, SC-007)."""

import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from ferro import __version__
from tests.conftest import EXAMPLES

HELLO = EXAMPLES / "hello_world"


def run_cli(*args: str, cwd: Path = HELLO) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ferro", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def test_version():
    result = run_cli("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == f"Ferro {__version__}"


def test_routes_lists_table_without_serving():
    result = run_cli("routes")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert re.match(r"METHODS\s+PATH\s+HANDLER", lines[0])
    rows = [re.split(r"\s{2,}", line.strip()) for line in lines[1:]]
    assert ["GET, HEAD", "/", "app.home"] in rows
    assert ["POST", "/products", "app.create_product"] in rows
    assert ["GET, HEAD", "/products/{id}", "app.product"] in rows
    paths = [row[1] for row in rows]
    assert paths == sorted(paths)


def test_dotted_target(tmp_path):
    package = tmp_path / "myproject"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "main.py").write_text(
        "from ferro import App\napplication = App()\n\n"
        "@application.get('/')\nasync def index():\n    return {'ok': True}\n"
    )
    result = run_cli("routes", "myproject.main:application", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "myproject.main.index" in result.stdout


def test_routes_show_websockets(tmp_path):
    (tmp_path / "app.py").write_text(
        "from ferro import App\napp = App()\n\n"
        "@app.websocket('/chat')\nasync def chat(ws):\n    pass\n\n"
        "@app.get('/')\nasync def home():\n    return {}\n"
    )
    result = run_cli("routes", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()[1:]
    rows = [re.split(r"\s{2,}", line.strip()) for line in lines]
    assert rows == [["GET, HEAD", "/", "app.home"], ["WEBSOCKET", "/chat", "app.chat"]]


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("nonexistent:app", "could not import module 'nonexistent'"),
        ("app:missing", "module 'app' has no attribute 'missing'"),
        ("app", "invalid target 'app': expected 'module:attribute'"),
        ("app:HTTPException", "'app:HTTPException' is a type, not a ferro.App instance"),
    ],
)
def test_bad_targets_exit_2(target, message):
    result = run_cli("dev", target, "--no-reload")
    assert result.returncode == 2
    assert f"error: {message}" in result.stderr


def test_missing_module_exits_2_even_with_reload():
    result = run_cli("dev", "nonexistent:app")
    assert result.returncode == 2
    assert "error: could not import module 'nonexistent'" in result.stderr


def test_import_error_in_user_module_shows_traceback(tmp_path):
    (tmp_path / "app.py").write_text("import does_not_exist_module\n")
    result = run_cli("run", cwd=tmp_path)
    assert result.returncode == 2
    assert "Traceback" in result.stderr
    assert "does_not_exist_module" in result.stderr


def test_invalid_setting_exits_2():
    result = run_cli("run", "--workers", "0")
    assert result.returncode == 2
    assert "workers" in result.stderr


def test_port_in_use_exits_3():
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = holder.getsockname()[1]
        result = run_cli("run", "--port", str(port))
    assert result.returncode == 3
    assert f"could not bind to 127.0.0.1:{port}" in result.stderr


def test_run_is_production_and_dev_is_debug(ferro_cli):
    prod = ferro_cli("run", "--port", "0", cwd=HELLO)
    prod.wait_for(r"\(production mode, \d+ workers\)")
    body = httpx.get(f"{prod.base_url}/boom").json()["error"]
    assert set(body) == {"status", "message", "request_id"}
    prod.terminate()
    assert prod.finish() == 0

    dev = ferro_cli("dev", "--port", "0", "--no-reload", cwd=HELLO)
    body = httpx.get(f"{dev.base_url}/boom").json()["error"]
    assert body["type"] == "ValueError"
    dev.interrupt()
    assert dev.finish() == 0


def test_cli_option_beats_environment(ferro_cli):
    server = ferro_cli(
        "run", "--port", "0", "--workers", "3", cwd=HELLO, env={"FERRO_WORKERS": "1"}
    )
    server.wait_for(r"\(production mode, 3 workers\)")
    server.terminate()
    assert server.finish() == 0


def test_environment_beats_default(ferro_cli):
    server = ferro_cli("run", "--port", "0", cwd=HELLO, env={"FERRO_WORKERS": "2"})
    server.wait_for(r"\(production mode, 2 workers\)")
    server.terminate()
    assert server.finish() == 0


def test_access_log_line(ferro_cli):
    server = ferro_cli("run", "--port", "0", cwd=HELLO)
    response = httpx.get(f"{server.base_url}/products/5", headers={"x-request-id": "log-check-1"})
    assert response.status_code == 200
    line = server.wait_for(r"ferro\.access.*request_id=log-check-1").string
    assert "method=GET" in line
    assert "path=/products/5" in line
    assert "status=200" in line
    assert re.search(r"duration_ms=\d+\.\d+", line)
    assert "client=127.0.0.1" in line
    server.terminate()
    server.finish()


def test_graceful_shutdown_completes_in_flight_requests(ferro_cli, tmp_path):
    (tmp_path / "app.py").write_text(
        "import asyncio\nfrom ferro import App\napp = App()\nstarted = 0\n\n"
        "@app.get('/slow')\nasync def slow():\n"
        "    global started\n    started += 1\n"
        "    await asyncio.sleep(2)\n    return {'done': True}\n\n"
        "@app.get('/started')\nasync def count():\n    return {'started': started}\n"
    )
    server = ferro_cli("run", "--port", "0", "--workers", "4", cwd=tmp_path)
    results: list[int] = []

    def call() -> None:
        try:
            results.append(httpx.get(f"{server.base_url}/slow", timeout=30).status_code)
        except httpx.HTTPError as exc:
            results.append(repr(exc))  # type: ignore[arg-type]

    threads = [threading.Thread(target=call) for _ in range(50)]
    for thread in threads:
        thread.start()
    # Stop only once every request is in a handler (slow CI machines take a while to connect).
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if httpx.get(f"{server.base_url}/started", timeout=5).json()["started"] == 50:
            break
        time.sleep(0.05)
    server.terminate()
    for thread in threads:
        thread.join(30)
    assert results == [200] * 50
    assert server.finish() == 0
