"""ORM test fixtures. The `database` fixture runs every test on SQLite and PostgreSQL
(constitution VII backend parity; Feature 003 research R9)."""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator

import pytest

from ferro import db
from tests.support.postgres import EXE, PostgresServer, free_port, postgres_bin, temporary_cluster

# Older names, still imported by the benchmarks.
_free_port = free_port
_postgres_bin = postgres_bin
__all__ = ["EXE", "PostgresServer", "database", "postgres_server", "use_models"]


@pytest.fixture(scope="session")
def postgres_server() -> Iterator[PostgresServer | None]:
    """A PostgreSQL server for tests: FERRO_TEST_POSTGRES_URL, or a temporary local cluster."""
    if url := os.environ.get("FERRO_TEST_POSTGRES_URL"):
        psql = shutil.which("psql")
        yield PostgresServer(url, psql) if psql else None
        return
    with temporary_cluster() as server:
        yield server


@pytest.fixture(params=["sqlite", "postgres"])
def database(request: pytest.FixtureRequest) -> Iterator[str]:
    """A fresh empty database for one test: in-memory SQLite, or a new PostgreSQL schema."""
    if request.param == "sqlite":
        db.connect_now("sqlite:///:memory:")
    else:
        server = request.getfixturevalue("postgres_server")
        if server is None:
            pytest.skip("PostgreSQL not available (install it or set FERRO_TEST_POSTGRES_URL)")
        schema = f"t_{uuid.uuid4().hex[:12]}"
        server.execute(f'CREATE SCHEMA "{schema}"')
        db.connect_now(server.schema_url(schema), pool_size=4)
    yield request.param
    db.disconnect_now()


async def use_models(*models: type) -> None:
    """Creates the tables of ``models`` in the current database."""
    await db.create_tables(models)
