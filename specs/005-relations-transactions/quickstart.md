# Quickstart & Validation: Relationships, Transactions & Aggregation

```python
from ferro import models
from ferro.db import transaction
from ferro.models import F, Q, Count, Sum


class Category(models.Model):
    name = models.String(max_length=100)


class Tag(models.Model):
    name = models.String(max_length=50, unique=True)


class Product(models.Model):
    name = models.String(max_length=200)
    price = models.Decimal(max_digits=10, decimal_places=2)
    stock = models.Integer(default=0)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    tags = models.ManyToMany(Tag, related_name="products")
```

```bash
ferro makemigrations   # Created migrations/0001_initial.py (category, tag, product, product_tags)
ferro migrate --database-url sqlite:///shop.db
```

```python
phones = await Category.objects.create(name="Phones")
sale = await Tag.objects.create(name="sale")
p = await Product.objects.create(name="X1", price=Decimal("499.00"), stock=5, category=phones)
await p.tags.add(sale)

await Product.objects.filter(category__name="Phones", tags__name="sale")       # [p]
await Product.objects.select_related("category")                              # p.category.name
await Category.objects.annotate(n=Count("products")).filter(n__gt=0)          # [phones], n=1
await Product.objects.aggregate(value=Sum("price"))                           # {"value": Decimal("499.00")}

async with transaction():
    await Product.objects.filter(pk=p.pk, stock__gte=2).update(stock=F("stock") - 2)
```

Automated: `pytest tests/python/orm/test_relations.py tests/python/orm/test_many.py
tests/python/orm/test_transactions.py tests/python/orm/test_aggregates.py
tests/python/migrations/test_relations.py` (both databases); `python tests/benchmarks/run_005.py`.
