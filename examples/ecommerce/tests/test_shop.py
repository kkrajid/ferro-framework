"""The shop's own tests: `python -m pytest` in this directory."""

import asyncio
from decimal import Decimal

import pytest
from app import app
from models import Category, Order, Product

from ferro.testing import TestClient


async def seed() -> dict[str, int]:
    phones = await Category.objects.create(name="Phones", slug="phones")
    books = await Category.objects.create(name="Books", slug="books")
    made = {}
    for name, slug, category, price, stock in [
        ("Phone X", "phone-x", phones, "499.00", 5),
        ("Phone Mini", "phone-mini", phones, "299.99", 1),
        ("Rust Book", "rust-book", books, "39.90", 10),
        ("Old Book", "old-book", books, "5.00", 3),
    ]:
        product = await Product.objects.create(
            name=name, slug=slug, category=category, price=Decimal(price), stock=stock
        )
        made[slug] = product.pk
    await Product.objects.filter(slug="old-book").update(active=False)
    return made


@pytest.fixture
def shop(tmp_path):
    url = f"sqlite:///{(tmp_path / 'shop.db').as_posix()}"
    with TestClient(app, database_url=url) as client:
        client.ids = asyncio.run(seed())
        yield client


def sign_up(client, username: str) -> None:
    password = f"{username}-secret-pw"
    assert (
        client.post("/api/register", json={"username": username, "password": password}).status_code
        == 201
    )
    assert (
        client.post("/api/login", json={"username": username, "password": password}).status_code
        == 200
    )


def stock(slug: str) -> int:
    return asyncio.run(Product.objects.get(slug=slug)).stock


def test_catalogue(shop):
    assert [c["slug"] for c in shop.get("/api/categories").json()] == ["books", "phones"]
    listing = shop.get("/api/products").json()
    assert listing["count"] == 3, "inactive products are hidden"
    assert [p["slug"] for p in listing["results"]] == ["phone-mini", "phone-x", "rust-book"]
    phones = shop.get("/api/products?category=phones").json()["results"]
    assert {p["slug"] for p in phones} == {"phone-x", "phone-mini"}
    assert [p["slug"] for p in shop.get("/api/products?q=rust").json()["results"]] == ["rust-book"]
    assert shop.get("/api/products/phone-x").json()["price"] == "499.00"
    assert shop.get("/api/products/old-book").status_code == 404


def test_shopping_journey(shop):
    assert shop.get("/api/orders").status_code == 401
    sign_up(shop, "alice")
    assert shop.get("/api/me").json()["username"] == "alice"
    ids = shop.ids
    created = shop.post(
        "/api/orders",
        json={
            "items": [
                {"product_id": ids["phone-x"], "quantity": 2},
                {"product_id": ids["rust-book"], "quantity": 1},
            ]
        },
    )
    assert created.status_code == 201, created.text
    order = created.json()
    assert order["status"] == "pending"
    assert order["total"] == "1037.90"
    assert stock("phone-x") == 3 and stock("rust-book") == 9

    declined = shop.post(f"/api/orders/{order['id']}/pay", json={"card_number": "4111111111111111"})
    assert declined.status_code == 402 and declined.json()["status"] == "declined"
    assert shop.get(f"/api/orders/{order['id']}").json()["status"] == "pending"
    paid = shop.post(f"/api/orders/{order['id']}/pay", json={"card_number": "4242424242424242"})
    assert paid.status_code == 201
    assert paid.json() == {
        **paid.json(),
        "status": "approved",
        "amount": "1037.90",
        "card_last4": "4242",
    }
    assert shop.get(f"/api/orders/{order['id']}").json()["status"] == "paid"
    assert (
        shop.post(
            f"/api/orders/{order['id']}/pay", json={"card_number": "4242424242424242"}
        ).status_code
        == 409
    )
    assert shop.post(f"/api/orders/{order['id']}/cancel").status_code == 409
    assert [o["id"] for o in shop.get("/api/orders").json()] == [order["id"]]

    shop.post("/api/logout")
    sign_up(shop, "bob")
    assert shop.get(f"/api/orders/{order['id']}").status_code == 404, (
        "other customers' orders are hidden"
    )
    assert shop.get("/api/orders").json() == []


def test_out_of_stock_changes_nothing(shop):
    sign_up(shop, "carol")
    ids = shop.ids
    refused = shop.post(
        "/api/orders",
        json={
            "items": [
                {"product_id": ids["rust-book"], "quantity": 2},
                {"product_id": ids["phone-mini"], "quantity": 2},
            ]
        },
    )
    assert refused.status_code == 409
    assert "Phone Mini" in refused.json()["error"]["message"]
    assert stock("rust-book") == 10, "the whole order was rolled back"
    assert asyncio.run(Order.objects.count()) == 0


def test_cancel_restores_stock(shop):
    sign_up(shop, "dave")
    order = shop.post(
        "/api/orders", json={"items": [{"product_id": shop.ids["phone-mini"], "quantity": 1}]}
    ).json()
    assert stock("phone-mini") == 0
    assert shop.post(f"/api/orders/{order['id']}/cancel").json()["status"] == "cancelled"
    assert stock("phone-mini") == 1


def test_validation(shop):
    sign_up(shop, "erin")
    assert (
        shop.post(
            "/api/register", json={"username": "erin", "password": "another-pass"}
        ).status_code
        == 409
    )
    assert (
        shop.post("/api/register", json={"username": "frank", "password": "123"}).status_code == 422
    )
    assert shop.post("/api/orders", json={"items": []}).status_code == 422
    bad_quantity = {"items": [{"product_id": shop.ids["phone-x"], "quantity": 0}]}
    assert shop.post("/api/orders", json=bad_quantity).status_code == 422
    unknown = {"items": [{"product_id": 999999, "quantity": 1}]}
    assert shop.post("/api/orders", json=unknown).status_code == 404
