"""Feature 017: the user model in the admin — passwords are hashed, never shown, and only
superusers grant privileges (both databases)."""

from __future__ import annotations

from ferro.auth import Permission, User, create_permissions
from tests.python.admin.conftest import PASSWORD, logged_in, make_site, shop

# Every field a change form submits (the admin requires all editable ones).
SAVED = {
    "username": "clerk",
    "email": "c@x.example",
    "is_active": "on",
    "date_joined": "2026-01-01T00:00",
}


def site_with_users():
    site = make_site()
    site.register(User)
    return site


async def test_passwords_are_set_through_set_password(database, serve):
    await shop()
    client = logged_in(serve, site_with_users())
    boss = await User.objects.get(username="boss")
    page = client.get(f"/admin/auth_user/{boss.pk}/change")
    assert page.status_code == 200
    assert 'type="password"' in page.text
    assert boss.password not in page.text, "the hash is never shown"

    added = client.post(
        "/admin/auth_user/add",
        data={"username": "clerk", "password": "a long enough secret", "is_active": "on"},
    )
    assert added.status_code == 303, added.text
    clerk = await User.objects.get(username="clerk")
    assert clerk.password.startswith("$argon2id$")
    assert await clerk.check_password("a long enough secret")

    weak = client.post(
        f"/admin/auth_user/{clerk.pk}/change",
        data={"password": "123", **SAVED},
    )
    assert weak.status_code == 200 and "too weak" in weak.text
    unchanged = client.post(
        f"/admin/auth_user/{clerk.pk}/change",
        data={"password": "", **SAVED},
    )
    assert unchanged.status_code == 303
    clerk = await User.objects.get(username="clerk")
    assert clerk.email == "c@x.example"
    assert await clerk.check_password("a long enough secret"), "empty keeps the password"


async def test_only_superusers_grant_privileges(database, serve):
    await shop()
    await create_permissions([User])
    staff = await User.objects.create_user("staff", password=PASSWORD, is_staff=True)
    for codename in ("view_auth_user", "change_auth_user"):
        await staff.user_permissions.add(await Permission.objects.get(codename=codename))
    client = logged_in(serve, site_with_users(), username="staff")
    page = client.get(f"/admin/auth_user/{staff.pk}/change")
    assert page.status_code == 200
    assert 'name="is_superuser"' not in page.text
    response = client.post(
        f"/admin/auth_user/{staff.pk}/change",
        data={
            **SAVED,
            "username": "staff",
            "is_staff": "on",
            "is_superuser": "on",
            "user_permissions": [str(p.pk) for p in await Permission.objects.all()],
        },
    )
    assert response.status_code == 303
    staff = await User.objects.get(username="staff")
    assert not staff.is_superuser
    assert await staff.user_permissions.count() == 2, "permissions were not changed"
