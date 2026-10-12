# Research: MVP Acceptance — E-commerce Application

**Feature**: [spec.md](spec.md) | **Date**: 2026-10-11

## R1. Shape of the proof

- **Decision**: the shop lives in `examples/ecommerce/`, scaffolded by `ferro new ecommerce` and
  extended with Ferro APIs only. `tests/integration/test_ecommerce.py` repeats what a developer
  does: a fresh `ferro new ecommerce` in a temporary directory, the example's files copied over,
  then `makemigrations` (must report no changes), `migrate`, `createsuperuser`, `shell -c` (seed
  data), `dev`, the shopping journey over HTTP, the admin, and a clean stop — on SQLite and on a
  temporary PostgreSQL cluster. The project's own `python -m pytest` also runs.
- **Why `ferro dev` for the journey**: `ferro run` marks session cookies `Secure` (production is
  behind TLS), so a plain-HTTP client correctly does not send them back. The Definition of Done
  names `ferro dev`; production behaviour is covered by Features 006/007.

## R2. Stock and money

- Placing an order: one `transaction()`; per line a conditional
  `filter(pk=…, stock__gte=q).update(stock=F("stock") - q)` — zero rows means not enough stock,
  raising 409 rolls everything back. On PostgreSQL concurrent updates of the same row wait for
  each other and re-check the condition; on SQLite transactions take the write lock up front.
  Lines are processed in product-id order so concurrent orders lock rows in the same order.
- Money is `Decimal` end to end (fields `Decimal(10|12, 2)`, JSON strings like `"1037.90"`).

## R3. Payments

- Test method only: an even last digit is approved (201), odd declined (402); a payment row is
  recorded either way with the last four digits and a random reference. Paying moves the order to
  `paid` with a conditional update inside the payment's transaction, so two concurrent payments
  cannot both succeed.

## R4. Access rules

- Catalogue is public; orders need a session (`@login_required` → 401); a customer's queries are
  always filtered by `customer=request.user`, so other orders are 404, not 403 (no existence leak).
- Staff manage everything through `app.add_admin()`; all five models are registered.
