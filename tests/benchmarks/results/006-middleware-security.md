# Feature 006 results: middleware & security

Measured 2026-10-11 00:58 UTC on Windows-11-10.0.26200-SP0, CPython 3.13.12, release build, `ferro run --workers 4`, oha on the same machine.

| Criterion | Scenario | Result | Verdict |
|---|---|---|---|
| SC-002 | hello world, no middleware, 100 clients, median of 3x10 s | 15,887 req/s (15,418, 15,887, 16,937); 84% of the Rust-only baseline (18,916 req/s: 18,916, 18,394, 21,189) vs 76% in Feature 001; 0 failed requests in all runs | PASS |
| SC-002 | same with one pass-through middleware | 16,650 req/s (16,544, 16,650, 20,056; +5%) | reported |
| SC-004 | limiter: 2000000 checks over 1000 client addresses | 136 ns per check; after a burst of 100,000 addresses 100000 buckets, one hour later 1000 | PASS |
| SC-004 | hello world with a rate limit on (never exceeded), 100 clients | 15,441 req/s (15,441, 12,579, 19,402; -3% vs no limit) | reported |

Feature 001 measured 18,886 req/s on an earlier day. Single runs on this
machine vary by about 10%, so variants alternate and medians are compared; SC-002 uses
the share of the Rust-only baseline measured in the same session (the baseline runs
through the same engine adapter, including the new security policy).
