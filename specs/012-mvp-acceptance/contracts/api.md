# Contract: Shop API

JSON bodies; errors use Ferro's error envelope `{"error": {"status", "message", "request_id"}}`.

| Method | Path | Auth | Success | Errors |
|---|---|---|---|---|
| POST | `/api/register` | — | 201 `{"id", "username"}` | 409 taken, 422 missing/weak password |
| POST | `/api/login` | — | 200 `{"id", "username"}` + session cookie | 401 |
| POST | `/api/logout` | — | 200 `{"ok": true}` | |
| GET | `/api/me` | session | 200 `{"id", "username"}` | 401 |
| GET | `/api/categories` | — | 200 `[{"id", "slug", "name"}]` sorted by name | |
| GET | `/api/products?category=&q=&page=` | — | 200 `{"count", "page", "pages", "results": [product]}` (20 per page, active only, by name) | 422 page < 1 |
| GET | `/api/products/{slug}` | — | 200 product | 404 unknown or inactive |
| POST | `/api/orders` | session | 201 order | 401, 404 product, 409 stock (nothing changed), 422 items |
| GET | `/api/orders` | session | 200 `[order]` newest first | 401 |
| GET | `/api/orders/{id}` | session | 200 order | 401, 404 (not yours) |
| POST | `/api/orders/{id}/cancel` | session | 200 order (`cancelled`, stock restored) | 404, 409 not pending |
| POST | `/api/orders/{id}/pay` | session | 201 approved / 402 declined `{"payment", "status", "amount", "card_last4"}` | 404, 409 not pending, 422 card |

- product: `{"id", "slug", "name", "description", "price": "499.00", "stock", "category_id"}`
- order: `{"id", "status": "pending"|"paid"|"cancelled", "total": "1037.90", "created", "lines": [{"product_id", "quantity", "unit_price"}]}`
- Quantities 1–100 per line; repeated products are merged.
