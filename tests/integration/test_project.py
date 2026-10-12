"""Feature 009 — `ferro new`, `ferro shell`, `ferro db shell` and `.env` through the CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

EXPECTED = [
    "app.py",
    "models.py",
    "admin.py",
    "tests/test_app.py",
    "migrations/.gitkeep",
    ".env",
    ".env.example",
    ".gitignore",
    "pyproject.toml",
    "README.md",
]


def run(*args: str, cwd: Path, env: dict[str, str] | None = None, stdin: str | None = None):
    environment = {k: v for k, v in os.environ.items() if not k.startswith("FERRO_")}
    environment.update(env or {})
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        input=stdin,
    )


def ferro(*args: str, **kwargs):
    return run("-m", "ferro", *args, **kwargs)


def test_new_project_works_without_edits(tmp_path):
    created = ferro("new", "shop", cwd=tmp_path)
    assert created.returncode == 0, created.stderr
    project = tmp_path / "shop"
    for name in EXPECTED:
        assert (project / name).exists(), name
    assert "cd shop" in created.stdout and "ferro migrate" in created.stdout
    assert "sqlite:///db.sqlite3" in (project / ".env").read_text()
    assert ".env" in (project / ".gitignore").read_text().splitlines()

    made = ferro("makemigrations", cwd=project)
    assert made.returncode == 0, made.stderr
    assert "create table product" in made.stdout and "create table auth_user" in made.stdout
    migrated = ferro("migrate", cwd=project)
    assert migrated.returncode == 0, migrated.stderr
    assert (project / "db.sqlite3").exists(), ".env supplies the database URL"
    routes = ferro("routes", cwd=project)
    assert "/products" in routes.stdout and "/admin" in routes.stdout

    tests = run("-m", "pytest", "-q", "-p", "no:cacheprovider", cwd=project)
    assert tests.returncode == 0, tests.stdout + tests.stderr

    shell = ferro("shell", "-c", "print(await Product.objects.count())", cwd=project)
    assert shell.returncode == 0, shell.stderr
    assert shell.stdout.strip().splitlines()[-1] == "0"
    piped = ferro(
        "shell",
        cwd=project,
        stdin="p = await Product.objects.create(name='X', price=1)\nprint(p.pk, app is not None)\n",
    )
    assert piped.returncode == 0, piped.stderr
    assert piped.stdout.strip().splitlines()[-1] == "1 True"
    failing = ferro("shell", "-c", "1 / 0", cwd=project)
    assert failing.returncode == 1 and "ZeroDivisionError" in failing.stderr


def test_names_and_existing_directories(tmp_path):
    for name in ("my-shop!", "class", "json", "9lives"):
        result = ferro("new", name, cwd=tmp_path)
        assert result.returncode == 2, name
        assert not (tmp_path / name).exists()
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "keep.txt").write_text("mine")
    refused = ferro("new", "taken", cwd=tmp_path)
    assert refused.returncode == 2 and "not empty" in refused.stderr
    forced = ferro("new", "taken", "--force", cwd=tmp_path)
    assert forced.returncode == 0 and (tmp_path / "taken" / "app.py").exists()
    assert (tmp_path / "taken" / "keep.txt").read_text() == "mine"
    empty = tmp_path / "blog"
    empty.mkdir()
    here = ferro("new", ".", cwd=empty)
    assert here.returncode == 0, here.stderr
    assert '"blog"' in (empty / "app.py").read_text()


def test_env_file_and_environment_precedence(tmp_path):
    ferro("new", "portal", cwd=tmp_path)
    project = tmp_path / "portal"
    other = tmp_path / "other.db"
    ferro("makemigrations", cwd=project)
    result = ferro(
        "migrate", cwd=project, env={"FERRO_DATABASE_URL": f"sqlite:///{other.as_posix()}"}
    )
    assert result.returncode == 0, result.stderr
    assert other.exists() and not (project / "db.sqlite3").exists(), "the environment wins"
    (project / ".env").write_text("FERRO_DATABASE_URL=sqlite:///db.sqlite3\nnot valid\n")
    broken = ferro("routes", cwd=project)
    assert broken.returncode == 2 and "line 2" in broken.stderr


def test_db_shell(tmp_path):
    ferro("new", "portal", cwd=tmp_path)
    project = tmp_path / "portal"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if sys.platform == "win32":
        (bin_dir / "sqlite3.bat").write_text("@echo client %*\n")
    else:
        script = bin_dir / "sqlite3"
        script.write_text('#!/bin/sh\necho client "$@"\n')
        script.chmod(0o755)
    path_env = {"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}
    opened = ferro("db", "shell", cwd=project, env=path_env)
    assert opened.returncode == 0, opened.stderr
    assert "client" in opened.stdout and "db.sqlite3" in opened.stdout
    missing = ferro("db", "shell", cwd=project, env={"PATH": str(tmp_path / "nothing")})
    assert missing.returncode == 2 and "sqlite3" in missing.stderr
    memory = ferro(
        "db", "shell", cwd=project, env={**path_env, "FERRO_DATABASE_URL": "sqlite:///:memory:"}
    )
    assert memory.returncode == 2 and "memory" in memory.stderr
