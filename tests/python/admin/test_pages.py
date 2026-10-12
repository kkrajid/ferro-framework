"""Feature 008 US1 — index and list pages (both databases)."""

from __future__ import annotations

import time

import pytest

from ferro import ConfigurationError
from ferro.admin import AdminSite, ModelAdmin
from ferro.models.queryset import QuerySet
from tests.python.admin.conftest import Category, Product, logged_in, make_site, shop


async def test_index_lists_registered_models(database, serve):
    await shop()
    page = logged_in(serve).get("/admin")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    for table, label in (("ad8_product", "Product"), ("ad8_category", "Category")):
        assert f'href="/admin/{table}"' in page.text
        assert f">{label}<" in page.text


async def test_list_columns_search_filters_ordering(database, serve):
    data = await shop()
    client = logged_in(serve)
    page = client.get("/admin/ad8_product")
    assert page.status_code == 200
    for header in ("Name", "Price", "Category", "Active"):
        assert f"<th>{header}</th>" in page.text
    assert f'href="/admin/ad8_product/{data["x1"].pk}/change"' in page.text
    assert "499.00" in page.text and "Phones" in page.text
    assert "3 rows" in page.text
    found = client.get("/admin/ad8_product", params={"q": "x"}).text
    assert "X1" in found and "X2" in found and "Novel" not in found
    by_category = client.get("/admin/ad8_product", params={"q": "book"}).text
    assert "Novel" in by_category and "X1" not in by_category
    filtered = client.get("/admin/ad8_product", params={"category": data["books"].pk}).text
    assert "Novel" in filtered and "X1" not in filtered
    assert f'href="?category={data["phones"].pk}"' in page.text, "filter links"
    inactive = client.get("/admin/ad8_product", params={"active": "false"}).text
    assert "Novel" in inactive and "X2" not in inactive
    ordered = client.get("/admin/ad8_product", params={"o": "-price"}).text
    assert ordered.index("X2") < ordered.index("X1") < ordered.index("Novel")


async def test_pagination(database, serve):
    await shop()
    client = logged_in(serve, make_site(list_per_page=2))
    first = client.get("/admin/ad8_product", params={"o": "name"}).text
    assert "Novel" in first and "X1" in first and "X2" not in first
    assert 'href="?o=name&amp;page=2"' in first
    second = client.get("/admin/ad8_product", params={"o": "name", "page": 2}).text
    assert "X2" in second and "Novel" not in second
    assert client.get("/admin/ad8_product", params={"page": "zzz"}).status_code == 200


async def test_hostile_values_are_escaped(database, serve):
    data = await shop()
    hostile = '<script>alert("x")</script>'
    await Product.objects.filter(pk=data["x1"].pk).update(name=hostile)
    await Category.objects.filter(pk=data["phones"].pk).update(name='"><img src=x>')
    client = logged_in(serve)
    for url in ("/admin/ad8_product", f"/admin/ad8_product/{data['x1'].pk}/change"):
        text = client.get(url).text
        assert "<script>alert" not in text and "<img src=x>" not in text, url
        assert "&lt;script&gt;" in text
    search = client.get("/admin/ad8_product", params={"q": '"><b>x'}).text
    assert '"><b>x' not in search


async def test_list_page_queries_and_time(database, serve, monkeypatch):
    await shop()
    phones = await Category.objects.get(name="Phones")
    await Product.objects.bulk_create(
        Product(name=f"bulk {i}", price=i, category=phones) for i in range(2000)
    )
    client = logged_in(serve)
    calls: list[str] = []
    original = QuerySet._run

    async def counting(self, query):
        calls.append(self.model.__name__)
        return await original(self, query)

    monkeypatch.setattr(QuerySet, "_run", counting)
    started = time.perf_counter()
    page = client.get("/admin/ad8_product")
    elapsed = time.perf_counter() - started
    assert page.status_code == 200 and "2003 rows" in page.text
    assert len(calls) <= 4, calls
    assert elapsed < 1.0


def test_registration_errors():
    site = AdminSite()
    site.register(Category)
    with pytest.raises(ConfigurationError, match="already registered"):
        site.register(Category)
    with pytest.raises(ConfigurationError, match="no field 'nope'"):
        AdminSite().register(Product, ModelAdmin(list_display=["nope"]))
