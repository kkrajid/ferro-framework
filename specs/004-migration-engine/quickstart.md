# Quickstart & Validation: Migration Engine

```bash
cd examples/products_api
ferro makemigrations                                   # Created migrations/0001_initial.py
ferro migrate --database-url sqlite:///shop.db         # Applying 0001_initial... OK
# add `sku = models.String(max_length=40, null=True)` to Product
ferro makemigrations                                   # Created migrations/0002_add_product_sku.py
ferro migrate --database-url sqlite:///shop.db
ferro migration status --database-url sqlite:///shop.db
ferro rollback --database-url sqlite:///shop.db        # Reverting 0002_add_product_sku... OK
```

Same commands with `postgresql://...`. Automated: `pytest tests/python/migrations
tests/integration/test_migrations_cli.py`.
