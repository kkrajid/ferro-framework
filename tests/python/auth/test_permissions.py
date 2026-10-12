"""Feature 007 US3: permissions, groups and decorators (both databases)."""

from __future__ import annotations

import pytest

from ferro import App, ConfigurationError, auth, models
from ferro.auth import (
    Group,
    Permission,
    User,
    authenticate,
    create_permissions,
    login_required,
    permission_required,
    staff_required,
)
from tests.python.auth.conftest import auth_tables


class Gadget(models.Model):
    name = models.String(max_length=20)

    class Meta:
        table = "a7_gadget"


async def test_default_permissions_are_created_once(database):
    await auth_tables()
    assert await create_permissions([Gadget]) == 4
    assert await create_permissions([Gadget]) == 0
    codes = sorted(await Permission.objects.values_list("codename", flat=True))
    assert codes == ["add_a7_gadget", "change_a7_gadget", "delete_a7_gadget", "view_a7_gadget"]


async def test_direct_group_superuser_and_inactive_rules(database):
    await auth_tables()
    await create_permissions([Gadget])
    change = await Permission.objects.get(codename="change_a7_gadget")
    view = await Permission.objects.get(codename="view_a7_gadget")
    editors = await Group.objects.create(name="editors")
    await editors.permissions.add(change)
    ann = await User.objects.create_user("ann", password="correct horse")
    assert not await ann.has_perm("change_a7_gadget")
    await ann.groups.add(editors)
    ann = await User.objects.get(pk=ann.pk)
    assert await ann.has_perm("change_a7_gadget")
    assert not await ann.has_perms(["change_a7_gadget", "view_a7_gadget"])
    await ann.user_permissions.add(view)
    ann = await User.objects.get(pk=ann.pk)
    assert await ann.has_perms(["change_a7_gadget", "view_a7_gadget"])
    boss = await User.objects.create_superuser("boss", "another secret")
    assert await boss.has_perm("anything_at_all")
    await User.objects.filter(pk=boss.pk).update(is_active=False)
    assert not await (await User.objects.get(pk=boss.pk)).has_perm("view_a7_gadget")


def guarded_site() -> App:
    app = App()
    app.add_auth()

    @app.post("/login")
    async def sign_in(request):
        data = await request.json()
        await auth.login(request, await authenticate(data["username"], data["password"]))
        return {}

    @app.get("/private")
    @login_required
    async def private(request):
        return {"ok": True}

    @app.get("/staff")
    @staff_required
    async def staff(request):
        return {"ok": True}

    @app.put("/gadgets")
    @permission_required("change_a7_gadget")
    async def change(request):
        return {"ok": True}

    return app


async def test_decorators_return_401_and_403(database, serve):
    await auth_tables()
    await create_permissions([Gadget])
    await User.objects.create_user("ann", password="correct horse")
    client = serve(guarded_site(), debug=True)
    for path, method in (("/private", "GET"), ("/staff", "GET"), ("/gadgets", "PUT")):
        response = client.request(method, path)
        assert response.status_code == 401, path
        assert response.json()["error"]["message"] == "Authentication required"
    client.post("/login", json={"username": "ann", "password": "correct horse"})
    assert client.get("/private").status_code == 200
    assert client.get("/staff").status_code == 403
    assert client.put("/gadgets").status_code == 403
    ann = await User.objects.get(username="ann")
    await ann.user_permissions.add(await Permission.objects.get(codename="change_a7_gadget"))
    assert client.put("/gadgets").status_code == 200


def test_decorators_check_handlers():
    with pytest.raises(ConfigurationError, match="request"):

        @login_required
        async def no_request():
            return {}

    with pytest.raises(ConfigurationError, match="async def"):

        @permission_required("x")
        def sync(request):
            return {}
