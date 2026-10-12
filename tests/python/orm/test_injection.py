"""SC-003 — SQL injection attempts through every input are treated as data (FR-017)."""

import pytest

from ferro import models
from ferro.models import FieldError
from tests.python.orm.conftest import use_models

PAYLOADS = [
    "x'; DROP TABLE target; --",
    "' OR '1'='1",
    '"; DELETE FROM target; --',
    "' UNION SELECT 1, 2 --",
    "1; UPDATE target SET name = 'pwned'",
    "\\'; --",
    "%",
    "_",
    "*",
    "?",
    "[",
    "\\",
]


class Target(models.Model):
    name = models.String(max_length=100)
    rank = models.Integer(default=0)


async def table_rows() -> list[str]:
    return sorted(t.name for t in await Target.objects.all())


@pytest.mark.parametrize("payload", PAYLOADS)
async def test_values_are_stored_and_matched_literally(database, payload):
    await use_models(Target)
    await Target.objects.create(name="safe")
    created = await Target.objects.create(name=payload)
    assert (await Target.objects.get(pk=created.pk)).name == payload
    assert (await Target.objects.get(name=payload)).pk == created.pk

    for lookup in ["exact", "iexact", "contains", "icontains", "startswith", "endswith"]:
        matches = await Target.objects.filter(**{f"name__{lookup}": payload})
        assert payload in [m.name for m in matches], lookup
        assert "safe" not in [m.name for m in matches], lookup
    assert len(await Target.objects.filter(name__in=[payload, "nothing"])) == 1
    assert len(await Target.objects.exclude(name=payload)) == 1
    assert await Target.objects.filter(name__gt=payload).count() >= 0

    await Target.objects.filter(pk=created.pk).update(name=payload + payload)
    assert (await Target.objects.get(pk=created.pk)).name == payload + payload
    assert await table_rows() == sorted(["safe", payload + payload])


@pytest.mark.parametrize("payload", PAYLOADS)
def test_payloads_as_names_are_rejected_before_any_sql(payload):
    with pytest.raises(FieldError):
        Target.objects.order_by(payload)
    with pytest.raises(FieldError):
        Target.objects.filter(**{payload: 1})
    with pytest.raises(FieldError):
        Target(**{payload: 1})


async def test_wildcards_match_literally(database):
    await use_models(Target)
    for name in ["100%", "1000", "a_b", "axb", "x*y", "xzy", "q?", "qq", "[z]", "z"]:
        await Target.objects.create(name=name)

    async def matching(**lookup):
        return sorted(t.name for t in await Target.objects.filter(**lookup))

    assert await matching(name__contains="%") == ["100%"]
    assert await matching(name__icontains="%") == ["100%"]
    assert await matching(name__contains="_") == ["a_b"]
    assert await matching(name__iexact="A_B") == ["a_b"]
    assert await matching(name__contains="*") == ["x*y"]
    assert await matching(name__endswith="?") == ["q?"]
    assert await matching(name__startswith="[") == ["[z]"]
    assert await Target.objects.count() == 10
