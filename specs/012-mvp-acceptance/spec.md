# Feature Specification: MVP Acceptance — E-commerce Application

**Feature Branch**: `012-mvp-acceptance`

**Created**: 2026-10-11

**Status**: Draft

**Input**: User description: "Feature 012 from docs/roadmap.md (vision §21, MVP Definition of
Done): an e-commerce application (users, products, categories, orders, payments, authentication,
admin, PostgreSQL, migrations, REST API, transactions) built with only Ferro, created and run
with `ferro new ecommerce`, `ferro dev`, `ferro makemigrations`, `ferro migrate`,
`ferro createsuperuser`."

## Clarifications

### Session 2026-10-11

The user asked to continue without interruptions; recommended options were taken and recorded.

- Q: Where does the application live and how is it proven? → A: In `examples/ecommerce/`, created
  with `ferro new ecommerce` and then extended. An automated acceptance test starts from a fresh
  `ferro new ecommerce`, copies the example's own files over it and runs the whole Definition of
  Done command sequence and the API journey against a real server on PostgreSQL (and SQLite).
- Q: How are payments handled without a real payment provider? → A: A built-in "test" payment
  method that approves any card number ending in an even digit and declines others; no card data
  is stored, only the last four digits. Real providers are out of scope.
- Q: How is overselling prevented? → A: Placing an order runs in one transaction: each line's
  stock is decremented with a conditional update that only succeeds while enough stock remains;
  any line failing rolls the whole order back and answers 409 naming the product.
- Q: Who may do what? → A: Anyone may browse categories and active products; registered,
  logged-in customers may place, view and pay their own orders; staff manage everything in the
  admin. Customers never see other customers' orders (404).
- Q: How much of the API is in scope? → A: Register / login / logout / me; categories; products
  (filter by category, search, pagination); orders (create, list mine, detail, cancel unpaid);
  payment of an order. JSON only; the admin is the back office.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Build and run the shop with Ferro's commands (Priority: P1)

A developer creates the project with `ferro new ecommerce`, writes models, creates migrations,
migrates a PostgreSQL database, creates an administrator and runs the server — only Ferro
commands, no other tools.

**Why this priority**: This is the MVP Definition of Done itself.

**Independent Test**: Run the command sequence on an empty PostgreSQL database; the server starts
and its probes report ready.

**Acceptance Scenarios**:

1. **Given** an empty directory, **When** `ferro new ecommerce` runs and the example's files are
   placed in it, **Then** `ferro makemigrations` reports no changes against the committed
   migrations, `ferro migrate` creates every table on PostgreSQL and on SQLite, and
   `ferro createsuperuser` creates an administrator.
2. **Given** the migrated database, **When** `ferro run` (or `ferro dev`) starts, **Then** the
   readiness probe reports ready and the API answers.
3. **Given** the project, **Then** its own test suite passes with `python -m pytest`.

---

### User Story 2 - Customers shop through the REST API (Priority: P1)

A customer registers, logs in, browses categories and products, places an order for several
products, pays it and sees it as paid; stock goes down accordingly.

**Why this priority**: Proves models, relations, sessions, validation, transactions and the
REST style work together for a real business flow.

**Independent Test**: Drive the API with an HTTP client from registration to a paid order.

**Acceptance Scenarios**:

1. **Given** a new visitor, **When** they register and log in, **Then** `me` returns their
   account and later requests are authenticated by the session cookie.
2. **Given** products in categories, **When** a visitor lists products by category, searches by
   name or pages through results, **Then** only active products appear, in a stable order, with
   prices as exact decimals.
3. **Given** a logged-in customer, **When** they order 2 of product A and 1 of product B, **Then**
   the order is created with its lines, unit prices copied from the products, the exact total,
   status `pending`, and both products' stock reduced.
4. **Given** an order line asking for more than the stock, **Then** the order is refused with 409
   naming the product and no stock changes for any line.
5. **Given** a pending order, **When** the customer pays with an approved test card, **Then** a
   payment is recorded (amount = order total, last four digits only) and the order becomes `paid`;
   a declined card records a declined payment and leaves the order `pending`; paying a paid order
   is refused (409).
6. **Given** a pending order, **When** the customer cancels it, **Then** it becomes `cancelled`
   and its stock is restored; paid orders cannot be cancelled.
7. **Given** two customers, **Then** neither can see or pay the other's orders (404), and
   anonymous requests to order endpoints get 401.
8. **Given** many customers ordering the last items of a product at the same time, **Then** no
   more items are sold than were in stock.

---

### User Story 3 - Staff run the shop in the admin (Priority: P2)

Staff log in to the admin and manage categories, products, orders and payments: search, filter,
edit stock and prices, and look up orders.

**Why this priority**: The back office completes the MVP but customers can shop without it.

**Independent Test**: Log in to the admin as the superuser and use the model pages.

**Acceptance Scenarios**:

1. **Given** the superuser, **When** they log in to `/admin`, **Then** categories, products,
   orders, order lines and payments are listed with search and filters.
2. **Given** the admin, **When** staff change a product's stock or price, **Then** the API shows
   the new values.

### Edge Cases

- Ordering an inactive or unknown product → 400/404 naming the product, nothing created.
- Quantities must be whole numbers from 1 to 100; an empty order is refused (422).
- Money is computed with exact decimals; totals never show floating-point artefacts.
- The database being unavailable during checkout answers 503 and changes nothing (Feature 011).
- Registering an existing username → 409; weak passwords → 422 with the reason.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The application MUST be a project created by `ferro new ecommerce` and extended
  only with Ferro APIs (models, migrations, routes, auth, admin) — no other framework or ORM.
- **FR-002**: It MUST define categories, products (category, unique slug, decimal price, stock,
  active flag), orders (customer, status, total, timestamps), order lines (product, quantity,
  unit price) and payments (order, amount, status, last four digits, reference).
- **FR-003**: Its migrations MUST be generated by `ferro makemigrations`, committed, and apply
  cleanly with `ferro migrate` on PostgreSQL and SQLite.
- **FR-004**: The REST API MUST cover account (register, login, logout, me), catalogue
  (categories; products with category filter, search, pagination; product detail) and orders
  (create, list mine, detail, cancel, pay) with JSON bodies and Ferro's error envelope.
- **FR-005**: Creating an order and paying it MUST each run in one transaction; stock MUST never
  go below zero, including under concurrent orders.
- **FR-006**: The admin MUST register every model with useful list columns, search and filters.
- **FR-007**: The project MUST ship its own tests (run with `python -m pytest`), and the Ferro
  repository MUST contain an acceptance test running the full Definition of Done sequence and
  the shopping journey against a real server on PostgreSQL and SQLite.
- **FR-008**: The project README MUST document the commands to set it up and run it.

### Key Entities

- **Category**: name, slug; has many products.
- **Product**: category, name, unique slug, description, price (decimal), stock (count), active.
- **Order**: customer (user), status (`pending`, `paid`, `cancelled`), total (decimal), created,
  updated; has many lines and payments.
- **OrderLine**: order, product, quantity, unit price.
- **Payment**: order, amount, status (`approved`, `declined`), card last four, reference,
  created.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Starting from an empty directory and an empty PostgreSQL database, the Definition
  of Done command sequence completes without manual steps and the server reports ready.
- **SC-002**: The full shopping journey (register → login → browse → order → pay → paid order
  visible, stock reduced) succeeds end to end on PostgreSQL and on SQLite.
- **SC-003**: With 20 concurrent customers ordering a product that has 5 items in stock, exactly
  5 items are sold and every other attempt is refused with 409.
- **SC-004**: The project's own test suite and the repository's acceptance test pass.

## Assumptions

- The test payment method stands in for a real provider; integrating providers is post-MVP.
- Prices are in one currency; taxes, shipping and discounts are out of scope.
- The acceptance test uses the temporary PostgreSQL cluster the test suite already manages.
