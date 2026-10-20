# Quickstart: Pydantic Integration

```bash
pip install "ferro[pydantic]"
```

```python
# app.py
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from ferro import App, JSONResponse
from ferro.models import Decimal as DecimalField
from ferro.models import Model, String

app = App(database_url="sqlite:///shop.db")


class Product(Model):
    name = String(max_length=100)
    price = DecimalField(max_digits=10, decimal_places=2)


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    price: Decimal = Field(gt=0, decimal_places=2)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    price: Decimal


@app.post("/products")
async def create(data: ProductIn):
    product = await Product.objects.create(**data.model_dump())
    return JSONResponse(ProductOut.model_validate(product), status=201)


@app.get("/products")
async def products() -> list[ProductOut]:
    return await Product.objects.order_by("name")
```

```bash
ferro db create-tables && ferro dev
curl -s -X POST localhost:8000/products -H 'content-type: application/json' \
     -d '{"name": "Lamp", "price": "19.90"}'          # 201 {"id":1,"name":"Lamp","price":"19.90"}
curl -s -X POST localhost:8000/products -H 'content-type: application/json' \
     -d '{"name": "", "price": -1}'                   # 422, two details (name, price)
curl -s localhost:8000/products                       # [{"id":1,"name":"Lamp","price":"19.90"}]
```

Automated: `.venv/Scripts/python -m pytest tests/python/test_pydantic.py` — all pass.
