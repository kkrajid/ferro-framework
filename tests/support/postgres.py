"""PostgreSQL servers for tests: the binaries on this machine and temporary clusters."""

from __future__ import annotations

import glob
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

EXE = ".exe" if sys.platform == "win32" else ""


@dataclass
class PostgresServer:
    url: str
    psql: str
    pg_ctl: str | None = None
    data: str | None = None
    port: int | None = None
    log: str | None = None

    @property
    def can_restart(self) -> bool:
        return self.pg_ctl is not None

    def _pg_ctl(self, *args: str) -> None:
        assert self.pg_ctl and self.data, "only a temporary cluster can be stopped and started"
        # The server inherits pg_ctl's stdio; capturing it would block until the server exits.
        subprocess.run(
            [self.pg_ctl, "-D", self.data, *args],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def restart(self) -> None:
        self._pg_ctl("-m", "fast", "-w", "restart")

    def stop(self) -> None:
        self._pg_ctl("-m", "fast", "-w", "stop")

    def start(self) -> None:
        options = f"-p {self.port} -c listen_addresses=127.0.0.1"
        if sys.platform != "win32" and self.data:
            # The default socket directory (/var/run/postgresql) is often not writable by the
            # user running the tests (CI runners); keep the socket next to the cluster.
            options += f" -c unix_socket_directories={Path(self.data).parent}"
        log = ["-l", self.log] if self.log else []
        self._pg_ctl("-o", options, *log, "-w", "start")

    def execute(self, sql: str) -> None:
        subprocess.run(
            [self.psql, "-d", self.url, "-v", "ON_ERROR_STOP=1", "-q", "-c", sql],
            check=True,
            capture_output=True,
        )

    def query(self, sql: str) -> str:
        return subprocess.run(
            [self.psql, "-d", self.url, "-A", "-t", "-c", sql],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def schema_url(self, schema: str) -> str:
        separator = "&" if "?" in self.url else "?"
        return f"{self.url}{separator}sslmode=disable&options=-csearch_path%3D{schema}"


def postgres_bin() -> Path | None:
    candidates: list[Path] = []
    if os.environ.get("FERRO_TEST_PG_BIN"):
        candidates.append(Path(os.environ["FERRO_TEST_PG_BIN"]))
    if found := shutil.which("pg_ctl"):
        candidates.append(Path(found).parent)
    candidates += [
        Path(p) for p in sorted(glob.glob("C:/Program Files/PostgreSQL/*/bin"), reverse=True)
    ]
    candidates += [Path(p) for p in sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True)]
    for candidate in candidates:
        if all((candidate / f"{tool}{EXE}").exists() for tool in ("initdb", "pg_ctl", "psql")):
            return candidate
    return None


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def temporary_cluster() -> Iterator[PostgresServer | None]:
    """A new PostgreSQL cluster in a temporary directory (``None`` without the binaries),
    removed afterwards."""
    bin_dir = postgres_bin()
    if bin_dir is None:
        yield None
        return
    root = Path(tempfile.mkdtemp(prefix="ferro-pg-"))
    data = root / "data"
    initdb = [
        str(bin_dir / f"initdb{EXE}"),
        "-D",
        str(data),
        "-U",
        "ferro",
        "-A",
        "trust",
        "-E",
        "UTF8",
    ]
    # Unicode-aware case mapping (`iexact` on "ÉCOLE"): the builtin provider (PostgreSQL 17+),
    # else ICU, else glibc's C.UTF-8; a plain C locale only folds ASCII.
    for locale in (
        ["--locale-provider=builtin", "--builtin-locale=C.UTF-8"],
        ["--locale-provider=icu", "--icu-locale=und", "--locale=C.UTF-8"],
        ["--locale=C.UTF-8"],
        ["--no-locale"],
    ):
        if subprocess.run([*initdb, *locale], capture_output=True).returncode == 0:
            break
        shutil.rmtree(data, ignore_errors=True)
    else:
        raise RuntimeError("initdb failed with every locale setting")
    port = free_port()
    server = PostgresServer(
        f"postgresql://ferro@127.0.0.1:{port}/postgres",
        str(bin_dir / f"psql{EXE}"),
        str(bin_dir / f"pg_ctl{EXE}"),
        str(data),
        port,
        str(root / "server.log"),
    )
    server.start()
    try:
        yield server
    finally:
        subprocess.run(
            [server.pg_ctl, "-D", str(data), "-m", "fast", "-w", "stop"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        shutil.rmtree(root, ignore_errors=True)
