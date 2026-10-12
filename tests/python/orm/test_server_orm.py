"""User Story 5 — the ORM inside a running application (FR-019, FR-021, FR-025)."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest

from ferro import App, Response, db, models
from ferro.config import resolve
from ferro.server import ServerRunner


class ShopProduct(models.Model):
    name = models.String(max_length=100)
    price = models.Decimal(max_digits=10, decimal_places=2)
    stock = models.Integer(default=0)


def make_app(url: str | None) -> App:
    app = App(database_url=url) if url else App()

    @app.post("/products")
    async def create(request):
        data = await request.json()
        product = await ShopProduct.objects.create(
            name=data["name"], price=Decimal(data["price"]), stock=data.get("stock", 0)
        )
        return Response(product.to_dict(), status=201)

    @app.get("/products")
    async def listing(min_price: str | None = None):
        products = ShopProduct.objects.order_by("pk")
        if min_price is not None:
            products = products.filter(price__gte=Decimal(min_price))
        return [p.to_dict() for p in await products]

    @app.get("/products/{id}")
    async def detail(id: int):
        return (await ShopProduct.objects.get_or_404(id=id)).to_dict()

    @app.get("/fast")
    async def fast():
        return {"ok": True}

    return app


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite:///{(tmp_path / 'shop.db').as_posix()}"

    async def prepare() -> None:
        await db.connect(url)
        await db.create_tables([ShopProduct])
        await db.disconnect()

    asyncio.run(prepare())
    return url


def test_crud_over_http(serve, db_url):
    client = serve(make_app(db_url))
    created = client.post("/products", json={"name": "Laptop", "price": "75000.00", "stock": 3})
    assert created.status_code == 201
    assert created.json() == {"id": 1, "name": "Laptop", "price": "75000.00", "stock": 3}
    client.post("/products", json={"name": "Mouse", "price": "500.00"})

    assert [p["name"] for p in client.get("/products").json()] == ["Laptop", "Mouse"]
    assert [p["name"] for p in client.get("/products?min_price=1000").json()] == ["Laptop"]
    assert client.get("/products/2").json()["name"] == "Mouse"


def test_get_or_404(serve, db_url):
    response = serve(make_app(db_url)).get("/products/999")
    assert response.status_code == 404
    assert response.json()["error"]["message"] == "ShopProduct not found"


def test_server_connects_and_disconnects(db_url):
    app = make_app(db_url)
    settings = resolve(cli={"port": 0}, env={}, app=app._settings, mode="run")
    runner = ServerRunner(app, settings)
    runner.start()
    try:
        assert db.is_connected()
    finally:
        runner.stop()
        runner.wait(10)
        runner.close()
    assert not db.is_connected()


def test_missing_database_is_explained(serve):
    response = serve(make_app(None), debug=True).get("/products")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["type"] == "ConfigurationError"
    assert "no database configured" in error["exception"]


def test_concurrent_requests_and_responsive_fast_route(serve, db_url):
    client = serve(make_app(db_url), workers=4)
    for i in range(20):
        client.post("/products", json={"name": f"p{i}", "price": f"{i}.00"})
    latencies: list[float] = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        listings = [pool.submit(client.get, "/products") for _ in range(50)]
        while not all(f.done() for f in listings):
            started = time.monotonic()
            assert client.get("/fast").status_code == 200
            latencies.append(time.monotonic() - started)
        results = [f.result() for f in listings]
    assert all(r.status_code == 200 and len(r.json()) == 20 for r in results)
    assert max(latencies, default=0) < 0.5


def test_database_url_setting_precedence():
    settings = resolve(
        cli={"database_url": None},
        env={"FERRO_DATABASE_URL": "sqlite:///env.db"},
        app={"database_url": "sqlite:///app.db"},
        mode="run",
    )
    assert settings.database_url == "sqlite:///env.db"
    assert resolve(env={}, mode="run").database_url is None
    assert "database_url" not in settings.to_native()


async def test_to_dict(database):
    from tests.python.orm.conftest import use_models

    await use_models(ShopProduct)
    product = await ShopProduct.objects.create(name="Pen", price=Decimal("1.50"))
    assert product.to_dict() == {
        "id": product.pk,
        "name": "Pen",
        "price": Decimal("1.50"),
        "stock": 0,
    }
