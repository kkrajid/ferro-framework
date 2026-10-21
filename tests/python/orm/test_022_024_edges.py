"""Edge cases of Features 022-024 found missing in the verification pass (both databases)."""

from __future__ import annotations

import asyncio

import pytest

from ferro import db, models
from ferro.db import on_commit, transaction
from ferro.signals import post_save

from .conftest import use_models


class Doc(models.Model):
    name = models.String(max_length=40)
    body = models.JSON(null=True)

    class Meta:
        table = "t25_doc"


async def docs():
    await use_models(Doc)
    await Doc.objects.create(
        name="Ánna",
        body={
            "tags": ["poetry", "drama"],
            "address": {"city": "Kochi", "zip": "682001"},
            "active": True,
            "score": 7,
            "ratio": 0.5,
        },
    )
    await Doc.objects.create(name="ben", body={"tags": ["prose"], "active": False, "score": 3})


async def names(queryset) -> list[str]:
    return sorted(d.name for d in await queryset)


async def test_json_edge_cases(database):
    await docs()
    query = Doc.objects.filter
    assert await names(query(**{"body__tags__-1": "drama"})) == ["Ánna"]
    assert await names(query(**{"body__tags__-1": "prose"})) == ["ben"]
    assert await names(query(body__address__has_key="city")) == ["Ánna"]
    assert await names(query(body__address__city="Kochi")) == ["Ánna"]
    assert await names(query(body__active=True)) == ["Ánna"]
    assert await names(query(body__active=False)) == ["ben"]
    assert await names(query(body__score=7.0)) == ["Ánna"]
    assert await names(query(body__ratio__lt=1)) == ["Ánna"]
    assert await names(query(body__address__zip="682001")) == ["Ánna"]
    assert await names(query(body__address__zip=682001)) == [], "text is not a number"
    assert await names(query(body__contains={"address": {"city": "Kochi"}})) == ["Ánna"]
    assert await names(query(body__tags__contains="prose")) == ["ben"]


async def test_unicode_regex_and_literal_percent(database):
    await docs()
    assert await names(Doc.objects.filter(name__regex="^Án")) == ["Ánna"]
    assert await names(Doc.objects.filter(name__startswith="Á")) == ["Ánna"]
    # %s is a placeholder everywhere, even inside quotes: %%s writes a literal one.
    assert await db.fetch("SELECT '%%s' AS x") == [{"x": "%s"}]


async def test_on_commit_from_child_tasks_and_failing_receivers(database):
    await use_models(Doc)
    ran = []

    async def child(n: int) -> None:
        await Doc.objects.create(name=f"c{n}")
        await on_commit(lambda: ran.append(n))

    async with transaction():
        await asyncio.gather(child(1), child(2))
        assert ran == []
    assert sorted(ran) == [1, 2]

    def refuse(sender, instance, **kwargs):
        raise RuntimeError("receiver refused")

    post_save.connect(refuse, sender=Doc, weak=False)
    try:
        with pytest.raises(RuntimeError, match="receiver refused"):
            async with transaction():
                await Doc.objects.create(name="rolled back by its receiver")
    finally:
        post_save.disconnect(refuse, sender=Doc)
    assert not await Doc.objects.filter(name="rolled back by its receiver").exists()
