"""Feature 024: post_migrate after `ferro migrate` (both databases)."""

from __future__ import annotations

from ferro import models
from ferro.migrations import commands
from ferro.signals import post_migrate
from tests.python.migrations.conftest import model


async def test_post_migrate_lists_applied_migrations(database, tmp_path):
    seen = []

    async def seed(sender, applied, **kwargs):
        seen.append(list(applied))

    post_migrate.connect(seed, weak=False)
    try:
        item = model("PmItem", {"name": models.String(max_length=20)}, table="pm_item")
        commands.make_migrations(tmp_path, models=[item])
        assert await commands.migrate(tmp_path) == ["0001_initial"]
        assert await commands.migrate(tmp_path) == []
    finally:
        post_migrate.disconnect(seed)
    assert seen == [["0001_initial"], []]
