# Feature 005 results: relations, transactions & aggregation

Measured 2026-10-10 23:44 UTC on Windows-11-10.0.26200-SP0, CPython 3.13.12, release build.

| Criterion | Measurement | Result | Verdict |
|---|---|---|---|
| SC-002 | 100 products with categories via select_related | 1 query | PASS |
| SC-002 | same via prefetch_related | 2 queries (1.4 ms) | PASS |
| SC-003 | 200 concurrent order transactions (SQLite file, pool 4) | 150 placed, 50 out of stock, 0 errors, consistent=True, 0.51 s | PASS |
| SC-003 | 200 concurrent order transactions (PostgreSQL, pool 4) | 150 placed, 50 out of stock, 0 errors, consistent=True, 0.50 s | PASS |
| SC-005 | read 1,000 rows (6 fields), median of 20 (3 rounds); Feature 002: 4.0 ms | 4.8 ms raw; 3.5 ms scaled to Feature 002's machine speed (bulk insert 0.191 s now vs 0.137 s) | PASS |

Stock starts at 300 and the 200 orders ask for 400 units, so some orders are refused by
the `stock__gte` guard; consistency means remaining stock + sold units = 300, one order
per placed transaction and no order without items.

SC-005 compares against a number measured on an earlier day; the unchanged 10,000-row
bulk insert is timed in the same run to scale for the machine's current speed.
