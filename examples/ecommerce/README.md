# ecommerce

The Ferro MVP acceptance shop: categories, products, customers, orders, payments, a JSON API and
an admin back office — built only with [Ferro](../../README.md) and created with
`ferro new ecommerce`.

## Run it

```bash
cp .env.example .env                        # SQLite by default; set a postgresql:// URL for PostgreSQL
ferro makemigrations                        # "No changes detected": migrations are committed
ferro migrate                               # create the tables
FERRO_SUPERUSER_PASSWORD=... ferro createsuperuser --username admin --no-input
ferro dev                                   # http://127.0.0.1:8000/api/products and /admin/
python -m pytest                            # the shop's own tests
```

Add categories and products in the admin (`/admin`) or with `ferro shell`:

```python
phones = await Category.objects.create(name="Phones", slug="phones")
await Product.objects.create(
    name="Phone X", slug="phone-x", category=phones, price=Decimal("499.00"), stock=10
)
```

## API

| Method | Path | Notes |
|---|---|---|
| POST | `/api/register` | `{"username", "password", "email"?}` → 201; 409 if taken, 422 if the password is weak |
| POST | `/api/login` / `/api/logout` | session cookie |
| GET | `/api/me` | 401 when not logged in |
| GET | `/api/categories` | |
| GET | `/api/products?category=&q=&page=` | active products, 20 per page |
| GET | `/api/products/{slug}` | |
| POST | `/api/orders` | `{"items": [{"product_id", "quantity"}]}` → 201; 409 when stock is short (nothing changes) |
| GET | `/api/orders`, `/api/orders/{id}` | your own orders only |
| POST | `/api/orders/{id}/cancel` | pending orders; stock is restored |
| POST | `/api/orders/{id}/pay` | `{"card_number"}` — test payments: an even last digit is approved (201), odd declined (402) |

Orders and payments each run in one database transaction; stock is taken with a conditional
update, so concurrent customers can never buy more than is in stock. Only the last four card
digits are stored.
