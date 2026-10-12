# Data Model: MVP Acceptance (the shop)

| Model | Table | Fields | Relations |
|---|---|---|---|
| Category | `category` | name `String(100)`, slug `String(100)` unique | has products |
| Product | `product` | name `String(200)`, slug `String(200)` unique, description `Text`, price `Decimal(10,2)`, stock `Integer` ≥ 0 by the order logic, active `Boolean`, created `DateTime` | category → Category (PROTECT) |
| Order | `order` | status `String(20)` indexed (`pending`→`paid` / `cancelled`), total `Decimal(12,2)`, created, updated | customer → `ferro.auth.User` (PROTECT); has lines, payments |
| OrderLine | `order_line` | quantity `Integer` 1–100, unit_price `Decimal(10,2)` (copied at order time) | order → Order (CASCADE), product → Product (PROTECT) |
| Payment | `payment` | amount `Decimal(12,2)`, status `approved`/`declined`, card_last4 `String(4)`, reference unique, created | order → Order (CASCADE) |

State transitions of an order: `pending` → `paid` (approved payment) or `cancelled` (customer);
`paid` and `cancelled` are final. Migration: `migrations/0001_initial.py` (generated).
