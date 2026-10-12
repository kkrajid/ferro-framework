# Contract: SQL Compilation (SQLite dialect)

Generated SQL is part of the tested contract (SC-002). Placeholders are `?`; identifiers are
double-quoted. Model used below: `product(id, name, price, stock, active)`.

| Operation | SQL | Params |
|---|---|---|
| `await Product.objects.all()` | `SELECT "id", "name", "price", "stock", "active" FROM "product"` | `[]` |
| `filter(price__gte=1000, active=True).order_by("-price")` | `SELECT … FROM "product" WHERE ("price" >= ? AND "active" = ?) ORDER BY "price" DESC` | `[1000.0, 1]` |
| `exclude(name="x")` on nullable `name` | `… WHERE NOT ("name" = ? AND "name" IS NOT NULL)` | `["x"]` |
| `filter(name=None)` | `… WHERE ("name" IS NULL)` | `[]` |
| `filter(name__contains="a*b")` | `… WHERE ("name" GLOB ?)` | `["*a[*]b*"]` |
| `filter(name__icontains="50%")` | `… WHERE ("name" LIKE ? ESCAPE '\')` | `["%50\%%"]` |
| `filter(id__in=[])` | `… WHERE (0 = 1)` | `[]` |
| `qs[20:40]` | `… LIMIT ? OFFSET ?` | `[20, 20]` |
| `offset(5)` only | `… LIMIT -1 OFFSET ?` | `[5]` |
| `count()` | `SELECT COUNT(*) FROM "product" WHERE …` | |
| `count()` on window | `SELECT COUNT(*) FROM (SELECT "id" FROM "product" … LIMIT ? OFFSET ?)` | |
| `exists()` | `SELECT 1 FROM "product" WHERE … LIMIT 1` | |
| `create(...)` | `INSERT INTO "product" ("name", "price", "stock", "active") VALUES (?, ?, ?, ?) RETURNING "id"` | values |
| `filter(...).update(stock=0)` | `UPDATE "product" SET "stock" = ? WHERE …` | |
| `filter(...).delete()` | `DELETE FROM "product" WHERE …` | |
| `save()` existing | `UPDATE "product" SET "name" = ?, … WHERE "id" = ?` | |
| `delete()` instance | `DELETE FROM "product" WHERE "id" = ?` | `[pk]` |

DDL (`create-tables`):

```sql
CREATE TABLE "product" (
  "id" INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  "name" varchar(200) NOT NULL,
  "price" decimal(10, 2) NOT NULL,
  "stock" integer NOT NULL,
  "active" bool NOT NULL
);
CREATE INDEX "product_name_idx" ON "product" ("name");
```

`unique=True` adds `UNIQUE`; `null=True` omits `NOT NULL`. Values that look like SQL are always
parameters; no input is ever spliced into SQL text.
