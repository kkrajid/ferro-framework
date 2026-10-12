"""Migration commands through the CLI (contracts/cli-and-files.md)."""

import os
import subprocess
import sys
from pathlib import Path

V1 = """from ferro import App, models

app = App()


class Book(models.Model):
    title = models.String(max_length=100)
"""

V2 = V1 + "    year = models.Integer(null=True)\n"


def cli(*args: str, cwd: Path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("FERRO_")}
    return subprocess.run(
        [sys.executable, "-m", "ferro", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def test_full_cycle(tmp_path):
    (tmp_path / "app.py").write_text(V1)
    url = ["--database-url", "sqlite:///app.db"]

    made = cli("makemigrations", cwd=tmp_path)
    assert made.returncode == 0, made.stderr
    assert "Created migrations/0001_initial.py" in made.stdout
    assert "  - create table book" in made.stdout
    assert "No changes detected" in cli("makemigrations", cwd=tmp_path).stdout

    applied = cli("migrate", *url, cwd=tmp_path)
    assert applied.returncode == 0, applied.stderr
    assert "Applying 0001_initial... OK" in applied.stdout
    assert "No migrations to apply" in cli("migrate", *url, cwd=tmp_path).stdout

    (tmp_path / "app.py").write_text(V2)
    check = cli("makemigrations", "--check", cwd=tmp_path)
    assert check.returncode == 1
    assert "Changes detected" in check.stdout
    assert cli("makemigrations", cwd=tmp_path).returncode == 0
    assert cli("migrate", *url, cwd=tmp_path).returncode == 0

    status = cli("migration", "status", *url, cwd=tmp_path)
    assert status.stdout.splitlines() == ["[X] 0001_initial", "[X] 0002_add_book_year"]

    reverted = cli("rollback", *url, cwd=tmp_path)
    assert reverted.returncode == 0, reverted.stderr
    assert "Reverting 0002_add_book_year... OK" in reverted.stdout
    assert "[ ] 0002_add_book_year" in cli("migration", "status", *url, cwd=tmp_path).stdout


def test_create_tables_refuses_with_migrations(tmp_path):
    (tmp_path / "app.py").write_text(V1)
    cli("makemigrations", cwd=tmp_path)
    refused = cli("db", "create-tables", "--database-url", "sqlite:///x.db", cwd=tmp_path)
    assert refused.returncode == 2
    assert "uses migrations" in refused.stderr and "ferro migrate" in refused.stderr


def test_errors_and_exit_codes(tmp_path):
    (tmp_path / "app.py").write_text(V1)
    assert cli("migrate", cwd=tmp_path).returncode == 2  # no database URL
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001_bad.py").write_text("migration = 5\n")
    broken = cli("migrate", "--database-url", "sqlite:///x.db", cwd=tmp_path)
    assert broken.returncode == 1
    assert "error: 0001_bad.py must define `migration = Migration(...)`" in broken.stderr


def test_server_warns_about_unapplied_migrations(ferro_cli, tmp_path):
    (tmp_path / "app.py").write_text(V1)
    cli("makemigrations", cwd=tmp_path)
    server = ferro_cli("run", "--port", "0", "--database-url", "sqlite:///app.db", cwd=tmp_path)
    server.wait_for(r"1 unapplied migration\(s\): 0001_initial")
    server.terminate()
    assert server.finish() == 0
