"""User Story 5 — `ferro db create-tables` (FR-021, FR-022, contracts/cli-db.md)."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

APP = """from ferro import App, models

app = App()


class Customer(models.Model):
    name = models.String(max_length=50)


class OrderLine(models.Model):
    qty = models.Integer(index=True)
"""


def run_cli(*args: str, cwd: Path, env: dict[str, str] | None = None):
    full_env = {k: v for k, v in os.environ.items() if not k.startswith("FERRO_")}
    full_env.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "ferro", *args],
        cwd=cwd,
        env=full_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def project(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(APP)
    return tmp_path


def test_create_tables_then_exists(tmp_path):
    cwd = project(tmp_path)
    first = run_cli("db", "create-tables", "--database-url", "sqlite:///shop.db", cwd=cwd)
    assert first.returncode == 0, first.stderr
    assert first.stdout.splitlines() == ["created  customer", "created  order_line"]
    second = run_cli("db", "create-tables", "--database-url", "sqlite:///shop.db", cwd=cwd)
    assert second.returncode == 0, second.stderr
    assert second.stdout.splitlines() == ["exists   customer", "exists   order_line"]
    with sqlite3.connect(cwd / "shop.db") as raw:
        tables = {
            row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {row[1] for row in raw.execute("PRAGMA index_list(order_line)")}
    assert {"customer", "order_line"} <= tables
    assert "order_line_qty_idx" in indexes


def test_environment_url_and_cli_precedence(tmp_path):
    cwd = project(tmp_path)
    from_env = run_cli(
        "db", "create-tables", cwd=cwd, env={"FERRO_DATABASE_URL": "sqlite:///env.db"}
    )
    assert from_env.returncode == 0, from_env.stderr
    assert (cwd / "env.db").exists()
    cli_wins = run_cli(
        "db",
        "create-tables",
        "--database-url",
        "sqlite:///cli.db",
        cwd=cwd,
        env={"FERRO_DATABASE_URL": "postgres://ignored"},
    )
    assert cli_wins.returncode == 0, cli_wins.stderr
    assert (cwd / "cli.db").exists()


def test_missing_url_exits_2(tmp_path):
    result = run_cli("db", "create-tables", cwd=project(tmp_path))
    assert result.returncode == 2
    assert "error: no database URL" in result.stderr


def test_bad_scheme_exits_2(tmp_path):
    result = run_cli("db", "create-tables", "--database-url", "mysql://x", cwd=project(tmp_path))
    assert result.returncode == 2
    assert "unsupported database URL scheme 'mysql'" in result.stderr


def test_bad_target_exits_2(tmp_path):
    result = run_cli(
        "db", "create-tables", "nope:app", "--database-url", "sqlite:///x.db", cwd=project(tmp_path)
    )
    assert result.returncode == 2
    assert "could not import module 'nope'" in result.stderr


def test_unopenable_database_exits_1(tmp_path):
    result = run_cli(
        "db",
        "create-tables",
        "--database-url",
        "sqlite:///missing/dir/x.db",
        cwd=project(tmp_path),
    )
    assert result.returncode == 1
    assert "error: unable to open database file" in result.stderr
