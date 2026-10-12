"""Feature 008 US3 — login, staff access, permissions and headers (both databases)."""

from __future__ import annotations

from ferro import App
from ferro.auth import Permission, User, create_permissions
from tests.python.admin.conftest import PASSWORD, Product, logged_in, make_site, shop


def admin_client(serve):
    app = App()
    app.add_admin(make_site())
    return serve(app, debug=True)


async def test_anonymous_visitors_go_to_login(database, serve):
    await shop()
    client = admin_client(serve)
    for url in ("/admin", "/admin/ad8_product", "/admin/ad8_product/add"):
        response = client.get(url)
        assert response.status_code == 303, url
        assert response.headers["location"].startswith("/admin/login?next=")
    page = client.get("/admin/login", params={"next": "/admin/ad8_product"})
    assert page.status_code == 200 and 'name="password"' in page.text
    assert 'value="/admin/ad8_product"' in page.text


async def test_login_rules(database, serve):
    await shop()
    await User.objects.create_user("clerk", password=PASSWORD)
    client = admin_client(serve)
    wrong = client.post("/admin/login", data={"username": "boss", "password": "nope"})
    assert wrong.status_code == 200 and "correct username and password" in wrong.text
    clerk = client.post("/admin/login", data={"username": "clerk", "password": PASSWORD})
    assert clerk.status_code == 200 and "staff account" in clerk.text
    ok = client.post(
        "/admin/login",
        data={"username": "boss", "password": PASSWORD, "next": "https://evil.example/"},
    )
    assert ok.status_code == 303 and ok.headers["location"] == "/admin"
    assert client.get("/admin").status_code == 200
    out = client.post("/admin/logout")
    assert out.status_code == 303
    assert client.get("/admin").status_code == 303


async def test_per_model_permissions(database, serve):
    await shop()
    await create_permissions([Product])
    staff = await User.objects.create_user("staff", password=PASSWORD, is_staff=True)
    client = logged_in(serve, username="staff")
    assert 'href="/admin/ad8_product"' not in client.get("/admin").text
    assert client.get("/admin/ad8_product").status_code == 403
    await staff.user_permissions.add(await Permission.objects.get(codename="view_ad8_product"))
    page = client.get("/admin/ad8_product")
    assert page.status_code == 200
    assert "/change" not in page.text and "/add" not in page.text, "no edit links"
    product = await Product.objects.first()
    assert client.get(f"/admin/ad8_product/{product.pk}/change").status_code == 403
    assert client.post(f"/admin/ad8_product/{product.pk}/delete").status_code == 403
    await staff.user_permissions.add(await Permission.objects.get(codename="change_ad8_product"))
    assert client.get(f"/admin/ad8_product/{product.pk}/change").status_code == 200


async def test_cross_site_posts_and_headers(database, serve):
    await shop()
    client = logged_in(serve)
    refused = client.post(
        "/admin/ad8_tag/add", data={"name": "new"}, headers={"origin": "https://evil.example"}
    )
    assert refused.status_code == 403
    page = client.get("/admin")
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    assert page.headers["cache-control"] == "no-store"


async def test_login_is_rate_limited(database, serve):
    await shop()
    client = admin_client(serve)
    statuses = [
        client.post("/admin/login", data={"username": "x", "password": "y"}).status_code
        for _ in range(11)
    ]
    assert statuses[:10] == [200] * 10 and statuses[10] == 429
