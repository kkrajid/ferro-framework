"""Checks an installed Ferro (a wheel in a clean virtual environment, no test dependencies):
the CLI, a project from `ferro new`, migrations, the server and the ORM.

    python scripts/smoke_test.py
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def ferro(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "ferro", *args], cwd=cwd, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise SystemExit(f"ferro {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def get(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read()


def main() -> None:
    import ferro as package

    print(f"ferro {package.__version__} from {Path(package.__file__).parent}")
    assert "site-packages" in package.__file__, "testing the installed package, not a checkout"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ferro("new", "shop", cwd=root)
        project = root / "shop"
        ferro("makemigrations", cwd=project)
        ferro("migrate", cwd=project)
        assert "/" in ferro("routes", cwd=project).stdout

        port = free_port()
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        server = subprocess.Popen(
            [sys.executable, "-m", "ferro", "run", "--port", str(port)],
            cwd=project,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=flags,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    status, body = get(f"http://127.0.0.1:{port}/healthz")
                    break
                except OSError:
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise SystemExit(f"server did not start:\n{server.communicate()}") from None
                    time.sleep(0.2)
            assert status == 200 and json.loads(body) == {"status": "ok"}, body
            status, _ = get(f"http://127.0.0.1:{port}/readyz")
            assert status == 200
        finally:
            if sys.platform == "win32":
                server.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                server.send_signal(signal.SIGTERM)
            _, err = server.communicate(timeout=60)
        assert server.returncode == 0, err
        assert "stopped:" in err, err

    os.environ.pop("FERRO_DATABASE_URL", None)
    import asyncio

    from ferro import db, models

    class SmokeItem(models.Model):
        name = models.String(max_length=20)

    async def orm() -> None:
        await db.connect("sqlite:///:memory:")
        await db.create_tables([SmokeItem])
        await SmokeItem.objects.create(name="ok")
        assert [i.name for i in await SmokeItem.objects.all()] == ["ok"]
        await db.disconnect()

    asyncio.run(orm())
    print("smoke test passed")


if __name__ == "__main__":
    main()
