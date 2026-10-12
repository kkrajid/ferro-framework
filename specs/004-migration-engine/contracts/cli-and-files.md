# Contract: Migration commands and files

```text
ferro makemigrations [TARGET] [--name NAME] [--check] [--empty] [--migrations-dir DIR]
ferro migrate        [TARGET] [--database-url URL] [--migrations-dir DIR]
ferro rollback       [TARGET] [--steps N | --to NAME|zero] [--database-url URL] [--migrations-dir DIR]
ferro migration status [TARGET] [--database-url URL] [--migrations-dir DIR]
```

Output:

```text
$ ferro makemigrations
Created migrations/0002_add_product_stock.py
  - add column stock to product
$ ferro migrate
Applying 0002_add_product_stock... OK
$ ferro migration status
[X] 0001_initial
[X] 0002_add_product_stock
[ ] 0003_alter_product_price
```

Exit codes: 0 success / no changes; 1 `--check` with pending changes, database or migration error;
2 configuration error (no database URL, bad target, invalid migration files).

Operations (`from ferro.migrations import ops`):

```python
ops.Column(name, kind, *, null=False, unique=False, primary_key=False, auto=False,
           max_length=None, max_digits=None, decimal_places=None)
ops.CreateTable(table, [Column, ...])            ops.DropTable(table)
ops.RenameTable(old, new)
ops.AddColumn(table, Column, default=<value>)    ops.RemoveColumn(table, column, default=<value>)
ops.AlterColumn(table, Column)                   ops.RenameColumn(table, old, new)
ops.CreateIndex(table, column, name=None)        ops.DropIndex(table, name)
```

`RemoveColumn(..., default=...)` makes the removal reversible for non-null columns.
