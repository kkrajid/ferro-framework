"""Feature 019: Pydantic models as request bodies and responses."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

pydantic = pytest.importorskip("pydantic")

from pydantic import BaseModel, ConfigDict, Field, RootModel  # noqa: E402

from ferro import App, ConfigurationError, JSONResponse, Response  # noqa: E402
from ferro._ferro import json_dumps  # noqa: E402
from ferro.models import CASCADE, ForeignKey, ManyToMany, Model, String  # noqa: E402
from ferro.models import Decimal as DecimalField  # noqa: E402
from ferro.testing import TestClient  # noqa: E402

# -- schemas and models (module level: annotations are resolved from module globals) ----------


class Dimensions(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    price: Decimal = Field(gt=0, decimal_places=2)
    tags: list[str] = []
    dimensions: Dimensions | None = None


class Stamp(BaseModel):
    at: dt.datetime
    amount: Decimal


class PydCategory(Model):
    title = String(max_length=50)


class PydProduct(Model):
    name = String(max_length=50)
    price = DecimalField(max_digits=10, decimal_places=2)
    secret = String(max_length=50, default="internal")
    category = ForeignKey(PydCategory, on_delete=CASCADE, null=True, related_name="products")


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    title: str


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    price: Decimal


class ProductWithCategory(ProductOut):
    category: CategoryOut


class CategoryWithProducts(CategoryOut):
    products: list[ProductOut]


TABLES = [PydCategory, PydProduct]


def body_app(received: list) -> App:
    app = App()

    @app.post("/items")
    async def create(data: ItemIn, count: int = 1):
        received.append(data)
        return {"name": data.name, "count": count}

    @app.post("/optional")
    async def optional(data: ItemIn | None = None):
        return {"got": data is not None}

    @app.post("/sync")
    def sync_create(data: ItemIn):
        received.append(data)
        return data

    @app.put("/items/{id}")
    async def replace(request, id: int, data: ItemIn):
        return {"id": id, "name": data.name, "method": request.method}

    return app


# -- User Story 1: request bodies -------------------------------------------------------------


def test_a_valid_body_arrives_as_the_model():
    received: list = []
    with TestClient(body_app(received)) as client:
        body = {
            "name": "Lamp",
            "price": "19.90",
            "tags": ["home"],
            "dimensions": {"width": 3, "height": 4},
        }
        response = client.post("/items?count=2", json=body)
        assert response.status_code == 200, response.text
        assert response.json() == {"name": "Lamp", "count": 2}
        assert client.put("/items/7", json=body).json() == {
            "id": 7,
            "name": "Lamp",
            "method": "PUT",
        }
    item = received[0]
    assert isinstance(item, ItemIn)
    assert item.price == Decimal("19.90") and item.dimensions == Dimensions(width=3, height=4)


def test_every_problem_is_reported_in_one_422():
    with TestClient(body_app([])) as client:
        response = client.post(
            "/items?count=many",
            json={"name": "", "price": "-1", "tags": "x", "dimensions": {"width": 0, "height": 2}},
        )
    assert response.status_code == 422
    details = response.json()["error"]["details"]
    places = {(d["location"], d["param"]) for d in details}
    assert places == {
        ("query", "count"),
        ("body", "name"),
        ("body", "price"),
        ("body", "tags"),
        ("body", "dimensions.width"),
    }
    by_param = {d["param"]: d["problem"] for d in details}
    assert "at least 1 character" in by_param["name"]
    assert "greater than 0" in by_param["dimensions.width"]


def test_missing_optional_malformed_and_unsupported_bodies():
    with TestClient(body_app([])) as client:
        missing = client.post("/items")
        assert missing.status_code == 422
        assert missing.json()["error"]["details"] == [
            {"param": "data", "location": "body", "problem": "missing request body"}
        ]
        assert client.post("/optional").json() == {"got": False}
        assert client.post("/optional", json={"name": "a", "price": 1}).json() == {"got": True}
        malformed = client.post(
            "/items", content=b"{not json", headers={"content-type": "application/json"}
        )
        assert malformed.status_code == 400
        assert "JSON" in malformed.json()["error"]["message"]
        wrong = client.post("/items", content=b"name=a", headers={"content-type": "text/plain"})
        assert wrong.status_code == 415
        not_object = client.post("/items", json=[1, 2])
        assert not_object.status_code == 422
        assert not_object.json()["error"]["details"][0]["param"] == "data"


def test_form_bodies_and_sync_handlers():
    received: list = []
    with TestClient(body_app(received)) as client:
        form = client.post("/items", data={"name": "Mug", "price": "4.50", "tags": ["a", "b"]})
        assert form.status_code == 200, form.text
        single = client.post("/items", data={"name": "Cup", "price": "2", "tags": "solo"})
        assert single.status_code == 200, single.text  # list fields take every value
        synced = client.post("/sync", json={"name": "Pen", "price": "1.25"})
        assert synced.status_code == 200
        assert synced.json() == {"name": "Pen", "price": "1.25", "tags": [], "dimensions": None}
    assert received[0].tags == ["a", "b"]
    assert received[1].tags == ["solo"]
    assert received[2].name == "Pen"


def test_body_parameters_are_checked_at_registration():
    app = App()
    with pytest.raises(ConfigurationError, match="one body parameter"):

        @app.post("/two")
        async def two(first: ItemIn, second: Dimensions): ...

    with pytest.raises(ConfigurationError, match="WebSocket"):

        @app.websocket("/ws")
        async def socket(ws, data: ItemIn): ...


# -- User Story 2: responses --------------------------------------------------------------------


def test_returned_models_are_sent_as_json():
    app = App()
    stamp = Stamp(at=dt.datetime(2026, 10, 12, 9, 30, tzinfo=dt.UTC), amount=Decimal("1.50"))

    @app.get("/one")
    async def one():
        return stamp

    @app.get("/many")
    async def many():
        return {"items": [stamp, stamp], "count": 2}

    @app.post("/created")
    async def created():
        return JSONResponse(stamp, status=201)

    with TestClient(app) as client:
        response = client.get("/one")
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {"at": "2026-10-12T09:30:00Z", "amount": "1.50"}
        assert client.get("/many").json()["items"][1]["amount"] == "1.50"
        made = client.post("/created")
        assert made.status_code == 201 and made.json()["at"] == "2026-10-12T09:30:00Z"
    assert json_dumps(RootModel[list[int]]([1, 2])) == b"[1,2]"


def response_app() -> App:
    app = App()

    @app.post("/setup")
    async def setup():
        books = await PydCategory.objects.create(title="Books")
        await PydProduct.objects.create(name="Atlas", price=Decimal("30.00"), category=books)
        await PydProduct.objects.create(name="Novel", price=Decimal("12.50"), category=books)

    @app.get("/products/{id}")
    async def product(id: int) -> ProductOut:
        return await PydProduct.objects.get(pk=id)

    @app.get("/products")
    async def products() -> list[ProductOut]:
        return await PydProduct.objects.order_by("pk")

    @app.get("/maybe")
    async def maybe() -> ProductOut | None:
        return None

    @app.get("/raw")
    async def raw() -> ProductOut:
        return Response("plain", status=202)  # type: ignore[return-value]

    @app.get("/wrong")
    async def wrong() -> ProductOut:
        return {"id": "not a number"}  # type: ignore[return-value]

    @app.get("/nested/{id}")
    async def nested(id: int, load: bool = False) -> ProductWithCategory:
        query = PydProduct.objects.select_related("category") if load else PydProduct.objects
        return await query.get(pk=id)

    @app.get("/categories/{id}")
    async def category(id: int, load: bool = False) -> CategoryWithProducts:
        query = PydCategory.objects.prefetch_related("products") if load else PydCategory.objects
        return await query.get(pk=id)

    return app


def test_return_annotations_convert_orm_objects(capfd):
    with TestClient(
        response_app(), database_url="sqlite:///:memory:", create_tables=TABLES
    ) as client:
        client.post("/setup")
        one = client.get("/products/1")
        assert one.json() == {"id": 1, "name": "Atlas", "price": "30.00"}  # no `secret`
        assert [p["name"] for p in client.get("/products").json()] == ["Atlas", "Novel"]
        assert client.get("/maybe").status_code == 204
        raw = client.get("/raw")
        assert (raw.status_code, raw.text) == (202, "plain")
        assert client.get("/wrong").status_code == 500
        assert client.get("/nested/1?load=true").json()["category"] == {"title": "Books"}
        assert client.get("/nested/1").status_code == 500
        listed = client.get("/categories/1?load=true").json()
        assert [p["name"] for p in listed["products"]] == ["Atlas", "Novel"]
        assert client.get("/categories/1").status_code == 500
    log = capfd.readouterr().err
    assert "wrong" in log and "does not match its return annotation" in log
    assert "not a number" not in log, "returned values are not logged"
    # Unloaded relations say how to load them.
    assert "select_related('category')" in log
    assert "prefetch_related('products')" in log


class PydLabel(Model):
    text = String(max_length=20)


class PydPost(Model):
    title = String(max_length=50)
    labels = ManyToMany(PydLabel, related_name="posts")


class LabelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    text: str


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    title: str
    labels: list[LabelOut]


def test_many_to_many_lists_read_prefetched_objects():
    app = App()

    @app.post("/setup")
    async def setup():
        post = await PydPost.objects.create(title="Hello")
        for text in ("a", "b"):
            await post.labels.add(await PydLabel.objects.create(text=text))

    @app.get("/posts/{id}")
    async def post(id: int) -> PostOut:
        return await PydPost.objects.prefetch_related("labels").get(pk=id)

    tables = [PydLabel, PydPost]
    with TestClient(app, database_url="sqlite:///:memory:", create_tables=tables) as client:
        client.post("/setup")
        assert client.get("/posts/1").json() == {
            "title": "Hello",
            "labels": [{"text": "a"}, {"text": "b"}],
        }


# -- User Story 3: everywhere JSON is produced --------------------------------------------------


def test_models_in_sockets_and_json_dumps():
    app = App()

    @app.websocket("/ws")
    async def socket(ws):
        data = Dimensions.model_validate_json(await ws.receive())
        await ws.send_json(Dimensions(width=data.width * 2, height=data.height * 2))

    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send('{"width": 2, "height": 3}')
        assert ws.receive_json() == {"width": 4, "height": 6}
    assert json_dumps({"d": Dimensions(width=1, height=1)}) == b'{"d":{"width":1,"height":1}}'
