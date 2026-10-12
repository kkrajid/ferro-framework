"""Feature 013 US2: many-to-many relations through a custom link model, on SQLite and
PostgreSQL."""

from __future__ import annotations

import pytest

from ferro import _ferro, models
from ferro.models import FieldError
from ferro.models.base import registered_model_classes

from .conftest import use_models


class ThMusician(models.Model):
    name = models.String(max_length=30)

    class Meta:
        table = "th13_musician"


class ThBand(models.Model):
    name = models.String(max_length=30)
    members = models.ManyToMany(ThMusician, through="ThMembership", related_name="bands")

    class Meta:
        table = "th13_band"


class ThMembership(models.Model):
    band = models.ForeignKey(ThBand, on_delete=models.CASCADE, related_name="memberships")
    musician = models.ForeignKey(ThMusician, on_delete=models.CASCADE, related_name="memberships")
    role = models.String(max_length=20)
    since = models.Integer(default=2020)

    class Meta:
        table = "th13_membership"


class ThAccount(models.Model):
    name = models.String(max_length=30)
    following = models.ManyToMany(
        "self",
        through="ThFollow",
        through_fields=("follower", "followed"),
        related_name="followers",
    )

    class Meta:
        table = "th13_account"


class ThFollow(models.Model):
    follower = models.ForeignKey(ThAccount, on_delete=models.CASCADE, related_name="follows")
    followed = models.ForeignKey(ThAccount, on_delete=models.CASCADE, related_name="followed_by")
    muted = models.Boolean(default=False)

    class Meta:
        table = "th13_follow"
        unique_together = (("follower", "followed"),)


async def setup():
    await use_models(ThMusician, ThBand, ThMembership)
    ann, bob, cid = [await ThMusician.objects.create(name=n) for n in ("ann", "bob", "cid")]
    band = await ThBand.objects.create(name="loud")
    return band, ann, bob, cid


def test_the_custom_model_is_the_link():
    assert ThBand.members.through is ThMembership
    assert ThAccount.following.through is ThFollow
    tables = {qualname: table for qualname, table in _ferro.registered_models(None)}
    assert "th13_band_members" not in tables.values()
    assert not any(cls.__name__ == "Band_members" for cls in registered_model_classes())


async def test_add_with_through_defaults(database):
    band, ann, bob, cid = await setup()
    await band.members.add(ann, through_defaults={"role": "guitar", "since": 2019})
    await band.members.add(ann, through_defaults={"role": "bass"})  # already linked: kept once
    await band.members.add(bob, cid.pk, through_defaults={"role": "vocals"})
    links = await ThMembership.objects.order_by("musician__name").values_list(
        "musician__name", "role", "since"
    )
    assert links == [("ann", "guitar", 2019), ("bob", "vocals", 2020), ("cid", "vocals", 2020)]
    assert sorted(m.name for m in await band.members.all()) == ["ann", "bob", "cid"]
    assert [b.name for b in await ThBand.objects.filter(members__name="bob")] == ["loud"]
    assert [m.name for m in await ThMusician.objects.filter(bands__name="loud", name="cid")] == [
        "cid"
    ]
    # The link rows can be filtered through the relation as well.
    guitarists = await ThMusician.objects.filter(memberships__role="guitar")
    assert [m.name for m in guitarists] == ["ann"]


async def test_required_link_fields_need_values(database):
    band, ann, *_ = await setup()
    with pytest.raises(FieldError, match=r"ThMembership\.role.*through_defaults"):
        await band.members.add(ann)
    with pytest.raises(FieldError, match="role"):
        await band.members.set([ann])
    assert await ThMembership.objects.count() == 0


async def test_both_managers_remove_set_clear_and_prefetch(database):
    band, ann, bob, cid = await setup()
    other = await ThBand.objects.create(name="quiet")
    await band.members.set([ann, bob], through_defaults={"role": "x"})
    await cid.bands.add(band, other, through_defaults={"role": "drums"})
    loaded = await ThBand.objects.order_by("name").prefetch_related("members")
    assert [sorted(m.name for m in await b.members.all()) for b in loaded] == [
        ["ann", "bob", "cid"],
        ["cid"],
    ]
    found = await ThMusician.objects.filter(name="cid").prefetch_related("bands")
    assert sorted(b.name for b in await found[0].bands.all()) == ["loud", "quiet"]

    await band.members.remove(bob)
    await band.members.set([ann, cid], through_defaults={"role": "y"})  # cid kept, nothing added
    assert sorted(m.name for m in await band.members.all()) == ["ann", "cid"]
    roles = dict(await ThMembership.objects.filter(band=band).values_list("musician__name", "role"))
    assert roles == {"ann": "x", "cid": "drums"}
    await cid.bands.clear()
    assert await ThMembership.objects.filter(musician=cid).count() == 0
    await band.delete()
    assert await ThMembership.objects.count() == 0


async def test_self_referential_link_with_through_fields(database):
    await use_models(ThAccount, ThFollow)
    ann, bob, cid = [await ThAccount.objects.create(name=n) for n in ("ann", "bob", "cid")]
    await ann.following.add(bob, cid)  # every extra field has a default
    await bob.following.add(cid, through_defaults={"muted": True})
    assert sorted(a.name for a in await ann.following.all()) == ["bob", "cid"]
    assert sorted(a.name for a in await cid.followers.all()) == ["ann", "bob"]
    assert await ThFollow.objects.filter(muted=True).count() == 1
    assert [a.name for a in await ThAccount.objects.filter(followers__name="ann", name="bob")] == [
        "bob"
    ]


def test_link_model_errors():
    class Th13Left(models.Model):
        name = models.String(max_length=10)

        class Meta:
            table = "th13_left"

    with pytest.raises(FieldError, match=r"Th13Missing.*foreign key to Th13Left"):

        class Th13Right(models.Model):
            lefts = models.ManyToMany(Th13Left, through="Th13Missing")

            class Meta:
                table = "th13_right"

        class Th13Missing(models.Model):
            right = models.ForeignKey(Th13Right, on_delete=models.CASCADE)

            class Meta:
                table = "th13_missing"

    with pytest.raises(FieldError, match="through_fields"):

        class Th13Pair(models.Model):
            lefts = models.ManyToMany(Th13Left, through="Th13Double")

            class Meta:
                table = "th13_pair"

        class Th13Double(models.Model):
            pair = models.ForeignKey(Th13Pair, on_delete=models.CASCADE)
            one = models.ForeignKey(Th13Left, on_delete=models.CASCADE, related_name="+")
            two = models.ForeignKey(Th13Left, on_delete=models.CASCADE, related_name="+")

            class Meta:
                table = "th13_double"

    with pytest.raises(FieldError, match="through_fields"):
        models.ManyToMany(Th13Left, through_fields=("a", "b"))

    # A failed link does not break later definitions.
    class Th13Later(models.Model):
        name = models.String(max_length=10)

        class Meta:
            table = "th13_later"
