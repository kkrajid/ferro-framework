"""Feature 015 US3: the cache API, memory backend and database backend (SQLite and
PostgreSQL)."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ferro import cache as cache_module
from ferro import db
from ferro.cache import DatabaseCache, MemoryCache, cache, configure
from ferro.cache.models import CacheEntry
from tests.python.orm.conftest import database, postgres_server  # noqa: F401 - fixtures


async def exercise(backend) -> None:
    assert await backend.get("missing") is None
    assert await backend.get("missing", "dflt") == "dflt"
    await backend.set("k", {"a": [1, 2], "t": (3, 4)})
    assert await backend.get("k") == {"a": [1, 2], "t": [3, 4]}
    assert await backend.has_key("k")
    assert await backend.add("k", "other") is False
    assert await backend.add("new", 5) is True
    assert await backend.delete("new") is True
    assert await backend.delete("new") is False

    await backend.set("short", 1, timeout=0.2)
    await backend.set("forever", 1, timeout=None)
    await backend.set("never", 1, timeout=0)
    assert await backend.get("never") is None
    await asyncio.sleep(0.3)
    assert await backend.get("short") is None
    assert await backend.get("forever") == 1
    assert await backend.add("short", 2) is True, "an expired entry can be added again"

    calls = []

    async def build():
        calls.append(1)
        return {"built": True}

    assert await backend.get_or_set("lazy", build, timeout=60) == {"built": True}
    assert await backend.get_or_set("lazy", build, timeout=60) == {"built": True}
    assert calls == [1]
    assert await backend.get_or_set("plain", lambda: 7) == 7

    await backend.set("n", 10)
    assert await backend.incr("n") == 11
    assert await backend.decr("n", 5) == 6
    assert await backend.get("n") == 6
    with pytest.raises(ValueError, match="not found"):
        await backend.incr("absent")
    with pytest.raises(ValueError, match="not an integer"):
        await backend.incr("k")

    await backend.set_many({"m1": 1, "m2": 2})
    assert await backend.get_many(["m1", "m2", "m3"]) == {"m1": 1, "m2": 2}
    await backend.delete_many(["m1", "m2"])
    assert await backend.get_many(["m1", "m2"]) == {}

    with pytest.raises(TypeError, match="'bad' is not JSON-compatible"):
        await backend.set("bad", object())
    with pytest.raises(ValueError, match="non-empty strings"):
        await backend.set("", 1)
    with pytest.raises(ValueError, match="longer than 250"):
        await backend.get("x" * 251)

    await backend.clear()
    assert await backend.get("forever") is None


async def test_memory_backend():
    await exercise(MemoryCache())


async def test_memory_backend_evicts_least_recently_used():
    small = MemoryCache(max_entries=3)
    for key in "abc":
        await small.set(key, key)
    assert await small.get("a") == "a"  # now the most recent
    await small.set("d", "d")
    assert len(small) == 3
    assert await small.get("b") is None
    assert [await small.get(k) for k in "acd"] == ["a", "c", "d"]


async def test_values_are_copies():
    memory = MemoryCache()
    value = {"items": [1]}
    await memory.set("v", value)
    value["items"].append(2)
    got = await memory.get("v")
    got["items"].append(3)
    assert await memory.get("v") == {"items": [1]}


@pytest.mark.usefixtures("database")
async def test_database_backend():
    await db.create_tables([CacheEntry])
    backend = DatabaseCache()
    await exercise(backend)
    # Expired rows are removed when read.
    await backend.set("old", 1, timeout=0.1)
    await asyncio.sleep(0.2)
    assert await backend.get("old") is None
    assert await CacheEntry.objects.filter(key="old").count() == 0


@pytest.mark.usefixtures("database")
async def test_database_increments_are_atomic():
    await db.create_tables([CacheEntry])
    backend = DatabaseCache()
    await backend.set("hits", 0)
    await asyncio.gather(*(backend.incr("hits") for _ in range(50)))
    assert await backend.get("hits") == 50


@pytest.mark.usefixtures("database")
async def test_configure_selects_the_backend():
    await db.create_tables([CacheEntry])
    try:
        assert isinstance(configure("database"), DatabaseCache)
        await cache.set("shared", {"x": 1})
        assert await CacheEntry.objects.filter(key="shared").count() == 1
        assert isinstance(configure("memory", max_entries=5), MemoryCache)
        assert await cache.get("shared") is None
        assert cache.backend.max_entries == 5
        with pytest.raises(ValueError, match="unknown cache backend"):
            configure("redis")
    finally:
        configure("memory")


def test_database_cache_is_shared_between_processes(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'cache.db').as_posix()}"
    script = textwrap.dedent(
        f"""
        import asyncio, sys
        from ferro import db
        from ferro.cache import cache, configure
        from ferro.cache.models import CacheEntry

        async def main():
            await db.connect({url!r})
            configure("database")
            if sys.argv[1] == "write":
                await db.create_tables([CacheEntry])
                await cache.set("greeting", {{"from": "writer"}}, timeout=60)
            else:
                print(await cache.get("greeting"))
            await db.disconnect()

        asyncio.run(main())
        """
    )
    path = tmp_path / "use_cache.py"
    path.write_text(script, encoding="utf-8")
    for mode in ("write", "read"):
        result = subprocess.run(
            [sys.executable, str(path), mode], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "{'from': 'writer'}"


def test_module_exports():
    assert cache_module.DEFAULT_TIMEOUT == 300.0
    assert repr(cache).startswith("<cache ")
