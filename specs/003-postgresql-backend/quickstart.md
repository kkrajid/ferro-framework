# Quickstart & Validation: PostgreSQL Backend

```bash
# any PostgreSQL ≥ 13; example with a local server
createdb shop
cd examples/products_api
ferro db create-tables --database-url postgresql://postgres@localhost/shop
ferro dev --database-url postgresql://postgres@localhost/shop
```

Same requests as Feature 002 quickstart Scenario 2; prices keep exact decimals.

Automated validation:

```bash
uv run --python .venv pytest tests/python/orm          # every ORM test on sqlite and postgres
```

Expect `[postgres]` variants to run (not skip) on a machine with PostgreSQL binaries installed.
