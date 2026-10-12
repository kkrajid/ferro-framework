# Feature 002 measurements

- Date: 2026-10-10 08:16 UTC
- Machine: Windows-11-10.0.26200-SP0, 8 logical CPUs
- Python 3.13.12, release build, SQLite file database (WAL)

| Criterion | Scenario | Result | Verdict |
|---|---|---|---|
| SC-004 | read 1,000 rows (6 fields), median of 20 | 4.0 ms | PASS |
| SC-004 | bulk_create 10,000 rows | 0.137 s | PASS |
| SC-005 | 100 clients on list endpoint (15 s) + 10 clients on a detail route | 0 failed; list 1,973 req/s; detail p99 48.7 ms | PASS |
