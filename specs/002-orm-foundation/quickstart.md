# Quickstart & Validation: ORM Foundation on SQLite

Contracts: [python-orm-api](contracts/python-orm-api.md),
[sql-compilation](contracts/sql-compilation.md), [cli-db](contracts/cli-db.md).

## Build and gates

Same as Feature 001 ([../001-runtime-core/quickstart.md](../001-runtime-core/quickstart.md)).

## Scenario 1 — Script usage (US1, US2, US4)

```python
import asyncio
from decimal import Decimal
from ferro import db, models

class Product(models.Model):
    name = models.String(max_length=200)
    price = models.Decimal(max_digits=10, decimal_places=2)
    stock = models.Integer(default=0)

async def main():
    await db.connect("sqlite:///:memory:")
    await db.create_tables()
    p = await Product.objects.create(name="Laptop", price=Decimal("75000.00"), stock=10)
    print(await Product.objects.filter(price__gte=1000).order_by("-price"))
    print(Product.objects.filter(price__gte=1000).sql())
    await db.disconnect()

asyncio.run(main())
```

Expect one product listed and `('SELECT "id", "name", "price", "stock" FROM "product" WHERE ("price" >= ?)', [1000.0])`.

## Scenario 2 — Web API (US5)

```bash
cd examples/products_api
ferro db create-tables --database-url sqlite:///shop.db      # created product
ferro dev --database-url sqlite:///shop.db
curl -X POST -H "content-type: application/json" -d '{"name":"Laptop","price":"75000.00"}' http://127.0.0.1:8000/products
curl http://127.0.0.1:8000/products?min_price=1000
curl -i http://127.0.0.1:8000/products/999                   # 404 envelope
```

## Scenario 3 — Load (SC-004, SC-005)

```bash
python tests/benchmarks/run_002.py
```

Records 1,000-row read time, 10,000-row bulk_create time and the 100-client list endpoint run in
`tests/benchmarks/results/002-orm-foundation.md`.
