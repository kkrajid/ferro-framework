# Feature Specification: Relationships, Transactions & Aggregation

**Feature Branch**: `005-relations-transactions`

**Created**: 2026-10-10

**Status**: Draft

**Input**: User description: "Feature 005 from docs/roadmap.md: ForeignKey, OneToOne, ManyToMany,
cross-relation lookups, `transaction()`, `Count/Sum/Avg/Min/Max`, `Q`/`F`, with migration support
for foreign keys (vision §7–8, §10). The user asked for Django-like simplicity of models."

## Clarifications

### Session 2026-10-10

The user asked to continue without interruptions; recommended options were taken and recorded.

- Q: What does reading `product.category` do when the related row was not loaded? → A: It raises a
  clear error naming `select_related("category")` and `await product.fetch_related("category")`.
  Attribute access never runs a hidden query (in async code it cannot; Django's async mode
  behaves the same way). `product.category_id` is always available.
- Q: How are deletions of referenced rows handled? → A: By the database (`ON DELETE` on the
  foreign key), so `instance.delete()` and `queryset.delete()` behave the same and need no extra
  queries. `PROTECT` raises `ProtectedError`.
- Q: Default reverse names? → A: The reverse accessor is `<model>_set` and the reverse lookup name is
  `<model>` (model name in snake_case, e.g. `order_item_set` / `order_item__quantity`), unless
  `related_name` is given, in which case both use it — Django's rule with Ferro's snake_case
  naming.
- Q: Do filters across multi-valued relations (reverse foreign keys, many-to-many) return
  duplicate rows? → A: No. Conditions in one `filter()` call must match the same related row,
  conditions in separate calls may match different rows (Django's rule), and each record is
  returned once.
- Q: What type does `Avg` return? → A: `float` for every numeric field (the same on both
  databases); `Sum`, `Min` and `Max` keep the field's type (`Decimal` stays `Decimal`); `Count` is
  `int`. Aggregates over no rows return `None` (`Count` returns 0) unless `default=` is given.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Foreign keys (Priority: P1)

```python
class Category(models.Model):
    name = models.String(max_length=100)

class Product(models.Model):
    name = models.String(max_length=200)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="products")

phone = await Product.objects.create(name="Phone", category=electronics)
cheap = await Product.objects.filter(category__name="Electronics", price__lt=500)
products = await Product.objects.select_related("category").order_by("category__name")
await electronics.products.filter(price__gt=1000).count()
```

**Why this priority**: Almost every real application (orders → customers, products → categories)
needs relations; without them the ORM cannot model the MVP e-commerce app.

**Independent Test**: Define two related models, migrate, create rows, and exercise assignment,
lookups across the relation in both directions, `select_related`, the reverse manager and every
`on_delete` behaviour on SQLite and PostgreSQL.

**Acceptance Scenarios**:

1. **Given** `ForeignKey(Category, on_delete=...)`, **Then** the table has a `category_id` column of
   the target key's type with a foreign key constraint and an index, created by `makemigrations` /
   `migrate` and by `create-tables`, on both databases.
2. **Given** `Product(category=electronics)` or `Product(category_id=3)`, **Then** saving stores the
   key; `product.category_id` reads it; assigning an unsaved instance or an instance of the wrong
   model raises a clear error.
3. **Given** a product loaded without its category, **When** reading `product.category`, **Then** a
   clear error explains how to load it; with `select_related("category")` (one query, a LEFT JOIN)
   or after `await product.fetch_related("category")` the instance is returned; a NULL key gives
   `None`.
4. **Given** `filter(category=obj)`, `filter(category_id=3)`, `filter(category__in=[...])`,
   `filter(category__isnull=True)`, `filter(category__name__icontains="tron")` and multi-hop paths
   (`category__parent__name=...`), **Then** the matching records are returned; `exclude()` with the
   same lookups returns exactly the complement (rows with NULL keys included).
5. **Given** the reverse side, **Then** `category.products` (or `category.product_set` without
   `related_name`) supports `all()`, `filter()`, `exclude()`, `count()`, `exists()`, `first()` and
   `create(...)` (which sets the key), and `Category.objects.filter(products__price__gt=1000)`
   returns each category once.
6. **Given** `on_delete=CASCADE`, `SET_NULL` (requires `null=True`), `PROTECT` and `DO_NOTHING`,
   **When** a category is deleted (instance or queryset), **Then** products are deleted, set to
   NULL, the delete is refused with `ProtectedError` (nothing deleted), or the database's
   constraint decides, respectively.
7. **Given** `order_by("category__name")`, **Then** results are ordered by the related column.
8. **Given** a self reference (`ForeignKey("self", ...)`) or a reference to a model defined later
   (`ForeignKey("Category", ...)`), **Then** the relation works once the target model exists.

---

### User Story 2 - Transactions (Priority: P1)

```python
from ferro.db import transaction

async with transaction():
    order = await Order.objects.create(customer=customer)
    await OrderItem.objects.create(order=order, product=phone, quantity=2)
    updated = await Product.objects.filter(pk=phone.pk, stock__gte=2).update(stock=F("stock") - 2)
    if not updated:
        raise OutOfStock()        # everything above is rolled back
```

**Why this priority**: Placing an order touches several tables; without atomicity a failure leaves
inconsistent data (an order without items, stock decremented without an order).

**Independent Test**: Run blocks that succeed, raise, nest and are cancelled, and verify what is
stored afterwards on both databases; run concurrent transactions.

**Acceptance Scenarios**:

1. **Given** a block that finishes normally, **Then** all its changes are committed together.
2. **Given** a block that raises, **Then** none of its changes are stored and the exception
   propagates unchanged.
3. **Given** nested `transaction()` blocks, **Then** an inner failure caught by the caller rolls
   back only the inner block (savepoint) and the outer block can still commit.
4. **Given** the task running a transaction is cancelled (timeout, client disconnect), **Then** the
   transaction is rolled back and its connection returned to the pool.
5. **Given** queries inside the block, including `bulk_create`, `update`, `delete` and queries
   started by tasks created inside the block, **Then** they all run in that transaction; queries
   from other requests never see uncommitted changes and never run inside it.
6. **Given** `@transaction()` on an async function, **Then** each call runs in its own transaction.
7. **Given** many concurrent requests each running a transaction, **Then** all complete without
   deadlocking the connection pool.

---

### User Story 3 - Aggregation, Q and F (Priority: P2)

```python
stats = await Product.objects.aggregate(total=Sum("price"), n=Count("id"), avg=Avg("price"))
busy = await Customer.objects.annotate(orders=Count("order")).filter(orders__gt=5).order_by("-orders")
per_category = await Product.objects.values("category__name").annotate(stock=Sum("stock"))
deals = await Product.objects.filter(Q(price__lt=100) | Q(tags__name="sale"), ~Q(stock=0))
await Product.objects.filter(pk=pk).update(stock=F("stock") - 1)
```

**Why this priority**: Reports, dashboards and safe counters need these; they complete the query
API expected from a Django-like ORM.

**Independent Test**: Load a known data set and compare every aggregate, annotation, grouping, Q
combination and F update with values computed in Python, on both databases.

**Acceptance Scenarios**:

1. **Given** `aggregate(...)` with `Count`, `Sum`, `Avg`, `Min`, `Max` over local fields or forward
   relation paths, **Then** one query returns a dict with the clarified types; `Count("x",
   distinct=True)` and `default=` work; filters and slices of the queryset are respected.
2. **Given** `annotate(n=Count("order"))` (or `Sum("items__quantity")`) on each record, **Then** each
   instance has the attribute, records without related rows get 0 / `None`, and the annotation can
   be used in `filter()`, `exclude()` and `order_by()`.
3. **Given** `values("category__name").annotate(total=Sum("price"))`, **Then** one row per group is
   returned as dicts; `values()` / `values_list(..., flat=True)` without annotations return plain
   values; filters on annotations of grouped queries apply to the groups.
4. **Given** `Q` objects combined with `&`, `|` and `~`, mixed with keyword lookups, **Then** the
   results equal the same logic evaluated in Python, including NULL values and relation lookups.
5. **Given** `F("field")` with `+ - * /` and constants, **Then** `update()` computes the new value in
   the database (atomic counters) and `filter(price__gt=F("cost"))` compares two columns.

---

### User Story 4 - Many-to-many and one-to-one (Priority: P2)

```python
class Product(models.Model):
    tags = models.ManyToMany(Tag, related_name="products")

class Profile(models.Model):
    user = models.OneToOne(Customer, on_delete=models.CASCADE, related_name="profile")

await phone.tags.add(sale, new)
await phone.tags.remove(new)
on_sale = await Product.objects.filter(tags__name="sale")
products = await Product.objects.prefetch_related("tags", "category__products")
```

**Why this priority**: Tags, categories and user profiles are common; they build on US1.

**Independent Test**: Create the models, link and unlink rows, query across the relation in both
directions and verify prefetched data and query counts on both databases.

**Acceptance Scenarios**:

1. **Given** `ManyToMany(Tag)`, **Then** migrations create a link table (`product_tags` with
   `product_id`, `tag_id`, unique pair, cascading deletes) and nothing on `product` or `tag`.
2. **Given** `add`, `remove`, `clear` and `set([...])` on either side, **Then** links are stored once
   (adding an existing link is a no-op), and `all()`, `filter()`, `count()` return the linked rows.
3. **Given** `filter(tags__name="sale")` and `filter(products__price__lt=10)` from the other side,
   **Then** matching records are returned once each.
4. **Given** `prefetch_related("tags")` (or a reverse foreign key, or nested paths), **Then** the
   related rows of all records are loaded with one extra query per relation level (two for
   many-to-many) and `await obj.tags.all()` returns them without querying.
5. **Given** `OneToOne(Customer, ...)`, **Then** the key column is unique; `profile.user` works like
   a foreign key; `customer.profile` returns the profile once loaded (`fetch_related` /
   `prefetch_related`) and raises `Profile.DoesNotExist` when there is none; a second profile for
   the same customer raises `IntegrityError`.
6. **Given** `Meta.unique_together = [("cart", "product")]`, **Then** the pair is unique in the
   database (migrations and `create-tables`).

### Edge Cases

- A foreign key to a model that is never defined: using the model raises an error naming the
  missing target.
- Reverse names clashing with a field or another reverse name: error at class definition naming
  both and suggesting `related_name`.
- Circular foreign keys between models created in the same migration: `makemigrations` stops and
  explains how to split it (make one side nullable and add it in a second migration).
- Deleting a row referenced through `PROTECT` inside a transaction: `ProtectedError`, and the
  transaction can still be rolled back or continue after a nested block.
- A query that fails inside a transaction on PostgreSQL aborts the transaction (database rule);
  later queries in the same block fail until it ends. A nested `transaction()` around the failing
  query keeps the outer one usable on both databases.
- A transaction left open because a task never ended (bug) is rolled back when its handle is
  garbage-collected; its connection is not leaked.
- Schema changes (`migrate`, `create-tables`) inside `transaction()` are refused with a clear error.
- `select_related` on a many-valued relation, aggregates over many-valued paths in `aggregate()` or
  grouped queries, and `F()` across relations: clear "not supported" errors suggesting the
  supported form.
- SQLite table rebuilds in migrations keep foreign keys intact and do not trigger cascading
  deletes; foreign key integrity is checked before the migration commits.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `ForeignKey(to, on_delete, *, related_name=None, null=False, column=None, default=...)`
  and `OneToOne(...)` MUST store the target's primary key in `<name>_id` (or `column`) with a
  database foreign key constraint and `ON DELETE` rule (`CASCADE`, `SET_NULL`, `PROTECT`,
  `DO_NOTHING`), indexed (unique for one-to-one). `to` may be a model, `"self"`, or a model name
  resolved when that model is defined.
- **FR-002**: Instances MUST expose the key as `<name>_id` and the related object as `<name>`
  (loaded via `select_related`, `prefetch_related`, `fetch_related` or assignment); access to an
  unloaded relation MUST raise `RelatedNotLoaded` without querying.
- **FR-003**: Reverse accessors MUST be installed on the target model: a manager for foreign keys
  and many-to-many relations, a single object for one-to-one.
- **FR-004**: Lookups MUST traverse forward foreign keys, reverse foreign keys, one-to-one and
  many-to-many relations to any depth, combined with every existing lookup; the relation itself
  accepts instances or keys (`category=obj`, `category__in=[...]`).
- **FR-005**: Conditions on multi-valued relations MUST follow Django's same-filter-call rule and
  MUST NOT duplicate records.
- **FR-006**: `select_related(*paths)` MUST load forward single-valued relations in the same query;
  `prefetch_related(*paths)` MUST load any relation with one query per level (two for
  many-to-many); `Model.fetch_related(*paths)` MUST do the same for one instance.
- **FR-007**: `ManyToMany(to, *, related_name=None)` MUST use an automatic link table
  `<table>_<field>` and provide `add`, `remove`, `clear`, `set` and query methods on both sides.
- **FR-008**: `ferro.db.transaction()` MUST work as an async context manager and decorator; nesting
  MUST use savepoints; every ORM operation in the task (and its child tasks) MUST use the
  transaction's connection; exceptions and cancellation MUST roll back.
- **FR-009**: `aggregate(**aggregates)` MUST return a dict; `annotate(**aggregates)` MUST add values
  per record (relation aggregates) or per group after `values()`; annotations MUST be usable in
  filters and ordering.
- **FR-010**: `values(*fields)` and `values_list(*fields, flat=False)` MUST return dicts / tuples,
  with relation paths allowed for forward relations.
- **FR-011**: `Q` objects with `&`, `|`, `~` MUST be accepted by `filter`, `exclude` and `get`, mixed
  with keyword lookups.
- **FR-012**: `F` expressions with arithmetic MUST be accepted in `update()` values and lookup
  values, referencing fields of the same model.
- **FR-013**: Migrations (`makemigrations`/`migrate`/`rollback`) and `create-tables` MUST create,
  alter and drop foreign keys, link tables and composite unique constraints on both databases,
  ordering table creation by dependencies; SQLite rebuilds MUST preserve and verify foreign keys.
- **FR-014**: `bulk_create(..., ignore_conflicts=True)` MUST skip rows that violate unique
  constraints (used by many-to-many `add`).
- **FR-015**: All relation, aggregate and expression SQL MUST be generated by the core with quoted
  identifiers and bound values (constitution IV).

### Key Entities

- **Relation**: source model and field, target model, kind (foreign key, one-to-one,
  many-to-many), delete rule, reverse name.
- **Link table**: the automatic model behind a many-to-many relation (two foreign keys and a unique
  pair).
- **Transaction**: a connection reserved for one task tree, with a stack of savepoints.
- **Aggregate / annotation**: function, target field path, distinct flag, default, alias.
- **Expression**: `F` field references combined with constants and arithmetic.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every acceptance scenario passes on SQLite and PostgreSQL with identical results.
- **SC-002**: Loading 100 products with their categories takes exactly one query with
  `select_related` and two with `prefetch_related`, regardless of the number of rows.
- **SC-003**: 200 concurrent order transactions (create order, items, decrement stock with `F`)
  finish with stock and order totals exactly consistent and no failed or stuck transactions.
- **SC-004**: Every aggregate, annotation, grouping and Q/F result in the test data set equals the
  value computed in Python from the same rows.
- **SC-005**: Existing ORM behaviour and performance are unchanged for queries that use no
  relations (all Feature 002–004 tests pass; the 1,000-row read benchmark stays within 10%).

## Assumptions

- Custom link models (`through=`), symmetrical self many-to-many, generic relations, model
  inheritance, `select_for_update`, `distinct()`, `only()/defer()` and on-commit hooks are out of
  scope.
- `PROTECT` is enforced by the database (`ON DELETE RESTRICT`); `SET_DEFAULT` is not offered.
- SQLite transactions start with `BEGIN IMMEDIATE` so read-then-write transactions cannot fail with
  lock-upgrade errors; writers are serialized (documented).
- `to_dict()` returns foreign keys as `<name>_id` values; nested serialization belongs to a later
  feature.
