"""Feature 007 US4: auth tables through migrations, default permissions, createsuperuser."""

import os
import subprocess
import sys
from pathlib import Path

APP = """from ferro import App, models
import ferro.auth

app = App()
app.add_auth()


class Book(models.Model):
    title = models.String(max_length=100)
"""


def cli(*args: str, cwd: Path, password: str | None = None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("FERRO_")}
    if password is not None:
        env["FERRO_SUPERUSER_PASSWORD"] = password
    return subprocess.run(
        [sys.executable, "-m", "ferro", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        stdin=subprocess.DEVNULL,
    )


def test_auth_through_the_cli(tmp_path):
    (tmp_path / "app.py").write_text(APP)
    url = ["--database-url", "sqlite:///app.db"]
    made = cli("makemigrations", cwd=tmp_path)
    assert made.returncode == 0, made.stderr
    for table in ("auth_user", "auth_group", "auth_permission", "auth_session", "book"):
        assert f"create table {table}" in made.stdout, table
    assert "create table auth_user_groups" in made.stdout

    migrated = cli("migrate", *url, cwd=tmp_path)
    assert migrated.returncode == 0, migrated.stderr
    # User, Group, Permission, Session, Book x add/change/delete/view (link tables excluded)
    assert "Created 20 permissions" in migrated.stdout
    assert "Created" not in cli("migrate", *url, cwd=tmp_path).stdout, "created once"

    weak = cli("createsuperuser", "--username", "admin", *url, cwd=tmp_path, password="123")
    assert weak.returncode == 1
    assert "at least 8 characters" in weak.stderr

    missing = cli("createsuperuser", "--username", "admin", "--no-input", *url, cwd=tmp_path)
    assert missing.returncode == 2
    assert "FERRO_SUPERUSER_PASSWORD" in missing.stderr

    created = cli(
        "createsuperuser",
        "--username",
        "admin",
        "--email",
        "a@x.io",
        *url,
        cwd=tmp_path,
        password="a-long-secret",
    )
    assert created.returncode == 0, created.stderr
    assert "Superuser 'admin' created" in created.stdout
    again = cli("createsuperuser", "--username", "admin", *url, cwd=tmp_path, password="x" * 9)
    assert again.returncode == 2 and "already exists" in again.stderr

    check = subprocess.run(
        [sys.executable, "-c", _VERIFY],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert check.stdout.strip() == "admin True True True", check.stderr


_VERIFY = """
import asyncio
from ferro import db
from ferro.auth import User, authenticate

async def main():
    await db.connect("sqlite:///app.db")
    user = await authenticate("admin", "a-long-secret")
    print(user.username, user.is_staff, user.is_superuser, await user.has_perm("view_book"))
    await db.disconnect()

asyncio.run(main())
"""
