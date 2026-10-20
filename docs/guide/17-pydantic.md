# Request and response models with Pydantic

Ferro has no serializer classes of its own: it works directly with [Pydantic](https://docs.pydantic.dev/)
models. Annotate a handler parameter with a model to receive a validated request body; annotate
the return type to send exactly the fields you declare.

```bash
pip install "ferro-framework[pydantic]"     # or: pip install pydantic   (Pydantic 2.7+)
```

Pydantic is optional. Ferro never imports it; nothing changes for applications that do not use it.

## Request bodies

```python
from decimal import Decimal

from pydantic import BaseModel, Field

from ferro import App

app = App()


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    price: Decimal = Field(gt=0, decimal_places=2)
    tags: list[str] = []


@app.post("/products")
async def create_product(data: ProductIn):
    product = await Product.objects.create(**data.model_dump(exclude={"tags"}))
    ...
```

- A parameter annotated with a Pydantic model receives the **request body** validated into that
  model. Path and query parameters keep working next to it
  (`async def update(id: int, data: ProductIn, notify: bool = False)`).
- JSON bodies (`content-type: application/json`, `*+json`, or none) are validated straight from
  the bytes by Pydantic's JSON parser. HTML form posts
  (`application/x-www-form-urlencoded`) are validated from the form fields; fields typed as lists
  take every value of their key.
- `data: ProductIn | None = None` makes the body optional (`None` when the request has none).
- One body parameter per handler; group several objects in one model. Plain `def` handlers work
  too — the body is validated before the handler runs on its worker thread.

### Errors

Invalid input is answered with Ferro's usual `422`, one detail per problem — path, query and body
problems together:

```json
{"error": {"status": 422, "message": "Validation failed", "request_id": "…",
  "details": [
    {"param": "count", "location": "query", "problem": "expected int, got 'many'"},
    {"param": "name", "location": "body", "problem": "String should have at least 1 character"},
    {"param": "dimensions.width", "location": "body", "problem": "Input should be greater than 0"}
  ]}}
```

| Request | Answer |
|---|---|
| no body, required model | `422`, `{"param": "data", "location": "body", "problem": "missing request body"}` |
| malformed JSON | `400` |
| another content type (`text/plain`, `multipart/form-data`, …) | `415` |
| body larger than `max_body_size` | `413`, before validation |

## Responses

Return Pydantic models directly — alone, in lists or dicts, or inside `JSONResponse` for another
status code. They are sent as JSON in Pydantic's JSON mode (decimals as strings, ISO 8601 dates):

```python
from ferro import JSONResponse


@app.post("/products")
async def create_product(data: ProductIn):
    product = await Product.objects.create(**data.model_dump(exclude={"tags"}))
    return JSONResponse(ProductOut.model_validate(product), status=201)
```

Models are accepted everywhere Ferro writes JSON: `ws.send_json(model)`,
`broadcast.publish(channel, model)` (subscribers receive the JSON as a dict), the cache.

### Response models: the return annotation

Annotate the return type with a model (or `list[Model]`, `Model | None`, …) and return anything
that fits — typically ORM objects. Ferro validates the result against the annotation, reading
objects by attribute, and sends **only the declared fields**:

```python
from pydantic import BaseModel, ConfigDict


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price: Decimal


@app.get("/products/{id}")
async def get_product(id: int) -> ProductOut:
    return await Product.objects.get_or_404(pk=id)        # other columns are not sent


@app.get("/products")
async def list_products(page: int = 1) -> list[ProductOut]:
    return await Product.objects.order_by("name")[(page - 1) * 20 : page * 20]
```

- This is the safe way to expose models: a column added later (or a password hash) is never sent
  by accident.
- Returning `None` still gives `204`; returning a `Response` (or `JSONResponse`, `EventStream`, …)
  sends it unchanged.
- A result that does not fit the annotation is a bug in the handler: `500`, logged with the
  handler name and Pydantic's explanation.

### Related objects

Nested schemas read relations, so load them first — attribute access never queries:

```python
class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    title: str


class ProductDetail(ProductOut):
    category: CategoryOut
    tags: list[TagOut]


@app.get("/products/{id}/detail")
async def product_detail(id: int) -> ProductDetail:
    return await Product.objects.select_related("category").prefetch_related("tags").get(pk=id)
```

Forgetting `select_related`/`prefetch_related` gives a `500` whose log says which relation to load.

## WebSocket messages

WebSocket handlers have no request body; validate each message yourself:

```python
import pydantic


@app.websocket("/ws/orders")
async def orders(ws):
    async for text in ws:
        try:
            order = OrderIn.model_validate_json(text)
        except pydantic.ValidationError as exc:
            await ws.send_json({"errors": exc.errors(include_url=False, include_context=False)})
            continue
        await ws.send_json(OrderOut(id=order.id, status="received"))
```

## Testing

Nothing special: `client.post("/products", json={...})` sends JSON, `data={...}` a form, and
`content=b"..."` a raw body with the `content-type` you choose in `headers`.

```python
def test_rejects_negative_prices():
    with TestClient(app, database_url="sqlite:///:memory:") as client:
        response = client.post("/products", json={"name": "Lamp", "price": "-1"})
        assert response.status_code == 422
        assert response.json()["error"]["details"][0]["param"] == "price"
```

## Not included

Pydantic v1; query-string models (use plain typed parameters); automatic schemas generated from
Ferro models; OpenAPI documentation.
