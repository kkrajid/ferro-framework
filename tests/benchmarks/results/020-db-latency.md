# Feature 020: e-commerce checkout latency

Measured 2026-10-20 on Windows 11 (8 logical CPUs, 5.9 GB RAM, desktop applications running),
CPython 3.13, PostgreSQL 18 (local temporary cluster), `oha` with 20 connections for 10 s against
`POST /api/orders` (one product, quantity 1) of `examples/ecommerce`, `ferro run --workers 4`
([`run_020.py`](../run_020.py)). All orders lock the same product row.

## Debug vs release build

| Build | Checkout p50 | p99 | req/s | One ORM query p50 |
|---|---|---|---|---|
| Debug (`maturin develop`, before this feature; served with `ferro dev`) | 243.8 ms | 2500 ms | 58 | 4.34 ms |
| Release (PyPI wheel 0.5.0rc2; `ferro dev`) | 87.0 ms | 241 ms | 205 | 0.84 ms |

The debug build is 3–5× slower. Nothing said so before this feature; `ferro run` and `ferro dev`
now print a warning when they load one.

## Before / after this feature (release builds, `ferro run`, interleaved rounds)

"Before" is the 0.5.0rc2 wheel with the old example; "after" is a release build of this feature
(BEGIN pipelined with the first statement, the order inserted once with its total).

| Round | Before p50 / req/s | After p50 / req/s |
|---|---|---|
| 1 | 39.18 ms / 459 | 38.66 ms / 461 |
| 2 | 39.74 ms / 449 | 41.47 ms / 426 |
| 3 | 39.11 ms / 459 | 39.31 ms / 454 |

No measurable difference on this machine: the database is local (round trips cost ~0.1 ms) and
the bottleneck is elsewhere (below). The saved round trips matter when the database is across a
network (0.5–2 ms each).

## Where the time goes (release build)

cProfile of the event-loop thread during the checkout load: the thread was busy 5.06 s of 5.1 s
(2.6 ms of Python CPU per checkout). About 40% of it was asyncio's Windows proactor waking up for
completions from Ferro's engine and database threads (`_loop_self_reading`,
`GetQueuedCompletionStatus`, `WSARecv`, the self-pipe `send`); the shop's handler, the ORM and the
authentication middleware together took about 10%. A selector event loop was not measurably
better (170–243 vs 210–235 req/s across alternating runs; this machine varies too much to tell).

Consequences: one process serves roughly 450 checkouts/s here, bounded by one Python thread;
`--processes N` multiplies it. Reducing the per-wake-up cost (fewer wake-ups per request, or a
faster event loop such as uvloop on Linux) is the next lever.

## Reference: asyncpg on the same cluster (no HTTP)

One SELECT p50 0.19–0.48 ms (Ferro 0.81–1.49 ms); a five-statement transaction 2.8–4.8 ms
(Ferro 4.2–4.8 ms), over two runs with large run-to-run variation.
