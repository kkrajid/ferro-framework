"""User Story 1 end to end through the CLI (quickstart Scenario 1)."""

import httpx

from tests.conftest import EXAMPLES


def test_ferro_dev_serves_hello_world(ferro_cli):
    server = ferro_cli("dev", "--port", "0", "--no-reload", cwd=EXAMPLES / "hello_world")
    banner = server.wait_for(r"Ferro \S+ serving app:app on http://127\.0\.0\.1:\d+ \(development")
    assert banner

    response = httpx.get(f"{server.base_url}/", timeout=10)
    assert response.status_code == 200
    assert response.json() == {"message": "Hello from Rust"}

    server.interrupt()
    assert server.finish() == 0
