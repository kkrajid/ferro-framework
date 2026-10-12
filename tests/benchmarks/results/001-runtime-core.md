# Feature 001 load results

- Date: 2026-10-10 07:05 UTC
- Machine: Windows-11-10.0.26200-SP0, AMD64 Family 23 Model 24 Stepping 1, AuthenticAMD, 8 logical CPUs
- Python 3.13.12, release build, `ferro run --workers 4 --log-level error`, client `oha 1.16.0` on the same machine
- Mode: full

| Criterion | Scenario | Result | Verdict |
|---|---|---|---|
| SC-002 | hello world, 100 clients, 30s | 18,886 req/s, 0 failed | PASS |
| SC-003 | hello world, 1000 clients, 60s | 0 failed; memory 30.1 -> 39.1 MiB (1.30x) | PASS |
| SC-004 | fast route p99 while 20 clients hit a 1 s sync handler | p99 3.54 ms, 0 failed | PASS |

## Context

- Rust-only baseline through the same adapter (`cargo run --release -p ferro-http --example
  baseline`), same machine and client, 100 clients: ~24,800 req/s, p50 3.3 ms. The Python path
  above runs at ~76% of it.
- Before bridge v2 (research R13) the same SC-002 run measured 4,225 req/s.
- Client and server share the same 8 logical CPUs, so absolute numbers are conservative.
