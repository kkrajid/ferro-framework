# Quickstart: ORM Completion

Run the feature's tests (SQLite and PostgreSQL; PostgreSQL needs local binaries or
`FERRO_TEST_POSTGRES_URL`):

```bash
cargo test -p ferro-orm --test locking_sql
.venv/Scripts/python -m pytest tests/python/orm/test_locking.py tests/python/orm/test_through.py tests/python/orm/test_inheritance.py -q
```

Try it:

```python
from ferro import models
from ferro.db import transaction


class Timestamped(models.Model):
    created = models.Integer(default=0)

    class Meta:
        abstract = True


class Product(Timestamped):
    name = models.String(max_length=50)
    stock = models.Integer(default=0)
    tags = models.ManyToMany("Tag", through="Tagging", related_name="products")


class Tag(Timestamped):
    name = models.String(max_length=20)


class Tagging(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
    weight = models.Integer()


async def buy(pk: int) -> bool:
    async with transaction():
        product = await Product.objects.select_for_update().get(pk=pk)
        if product.stock == 0:
            return False
        product.stock -= 1
        await product.save()
        return True


await product.tags.add(sale, through_defaults={"weight": 3})
```

Expected: `ferro makemigrations` creates `product`, `tag` and `tagging` (no `product_tags`), each
with a `created` column; concurrent `buy()` calls never sell more than the stock.
