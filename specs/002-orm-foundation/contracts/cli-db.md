# Contract: Database CLI and setting (Feature 002)

## Setting

| Setting | Option | Environment | Default |
|---|---|---|---|
| database_url | `--database-url URL` (dev, run, db) | `FERRO_DATABASE_URL` | none |

Precedence as in Feature 001: CLI > environment > `App(database_url=...)` > default.

## `ferro db create-tables [TARGET] [--database-url URL]`

Imports `TARGET` (default `app:app`) so its models register, connects, creates missing tables and
indexes for every registered model, prints one line per table, and exits.

```text
created  product
created  order_item
exists   customer
```

Exit codes: 0 success; 2 configuration error (no database URL, bad target, bad URL); 1 database
error (message on stderr, e.g. `error: unable to open database file`).
