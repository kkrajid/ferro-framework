# Feature 003 measurements

- Date: 2026-10-10 09:11 UTC
- Machine: Windows-11-10.0.26200-SP0, 8 logical CPUs; debug build (throughput not a criterion here); local PostgreSQL (C:\Program Files\PostgreSQL\18\bin)

| Criterion | Scenario | Result | Verdict |
|---|---|---|---|
| SC-003 | 100 clients on list endpoint, 15 s, pool_size=10 | 0 failed; 318 req/s; peak server connections 10 | PASS |
