# Quickstart & Validation: MVP Acceptance

```bash
ferro new ecommerce && cd ecommerce          # then use examples/ecommerce's files
ferro makemigrations                         # No changes detected (migrations committed)
ferro migrate
FERRO_SUPERUSER_PASSWORD=... ferro createsuperuser --username admin --no-input
ferro dev                                    # /api/products, /admin
python -m pytest                             # the shop's tests
```

Automated: `pytest tests/integration/test_ecommerce.py` — the whole sequence plus the shopping
journey, the 20-customer race for 5 items and the admin, on SQLite and PostgreSQL.
