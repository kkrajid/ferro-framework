# Research: Shared State Across Processes and a Cache API

## R1 — Sharing rate limits: shared memory with a lock-free table

**Decision**: A new crate `ferro-shared` maps a file created by the supervisor in a private
temporary directory (`memmap2`) and exposes a fixed table of 65,536 `AtomicU64` slots. Each slot
packs a 16-bit key fingerprint and a 48-bit "theoretical arrival time" in microseconds since the
table's creation. The limiter runs GCRA (generic cell rate algorithm) with one compare-and-swap
per request: emission interval `T = period / count`; a request is allowed when
`max(tat, now) + T - now <= period`, which is exactly the existing token bucket (burst of `count`,
refill `count` per `period`). Keys are hashed with a per-run secret stored in the file header, so
clients cannot aim at colliding slots. Lookup probes 8 slots: first the client's own entry, then
an empty or expired one. A full window falls back to the process limiter (logged once).

**Rationale**: Workers must agree on every request without a round trip; a CAS on shared memory
costs about as much as the current in-process mutex. GCRA needs one word of state, so the update
is a single atomic operation and no lock can be left held by a crashed worker. Expired entries
(`tat <= now`) are equivalent to a full bucket, so they can be reused without a sweeper.

**Alternatives**: limit divided by N per process (wrong for keep-alive clients, which stay on one
worker); asking the supervisor per request (IPC on the hot path); the database (a write per
request); a mutex in shared memory (a crashed holder blocks everyone).

**Known approximation**: two first requests of a new client racing in different processes can
create two entries for a moment, admitting one extra request; a 16-bit fingerprint collision
inside one probe window (1 in 65,536 per occupied slot) merges two clients' budgets (stricter,
never looser).

## R2 — Unsafe code

**Decision**: `ferro-shared` is the only crate with `unsafe` (mapping a file, viewing it as
`[AtomicU64]`), each block with a `SAFETY` comment: the mapping is page-aligned, its length is a
multiple of 8, it lives as long as the `Arc` holding it, and other processes touch it only through
atomic operations on 8-byte-aligned words (lock-free on every supported 64-bit target). Every
other crate keeps `#![forbid(unsafe_code)]`.

## R3 — Aggregating metrics: per-worker snapshots, merged on scrape

**Decision**: When metrics are on and the server is a worker of `--processes N`, the engine
writes its metrics text to `<dir>/worker-<pid>-<start>.prom` every second (write to a temporary
name, then rename) and once more when it stops. A worker answering `/metrics` renders its own
numbers live, reads the other snapshots and merges them by family and series: counters and
histogram lines are summed; `ferro_http_requests_in_flight` only counts snapshots updated in the
last 5 seconds (live workers); `ferro_process_start_time_seconds` takes the earliest; build info
is kept once. Snapshots of dead workers stay, so totals never decrease. The supervisor removes the
directory when it exits.

**Rationale**: Merging the rendered text covers every series — including the database metrics
the Python bridge renders — without moving any metric into shared memory or changing a layout.
One render per second per worker is negligible. Other workers' numbers can be up to one second
old, which is below any scrape interval in practice.

**Alternatives**: all counters in shared memory (a fixed layout shared by the engine and the
bridge, sized before the router exists); scraping workers individually (Prometheus would need one
target per worker, impossible behind one port).

## R4 — Passing the shared paths to workers

**Decision**: The supervisor creates `ferro-shared-<random>/` in the system temp directory
(owner-only permissions where the platform supports them), creates `rates.shm` through
`_ferro.create_shared_state(path)`, and passes the directory to workers with the internal
`_worker --shared-dir DIR` option. Workers add two internal native settings: `shared_state`
(the table) and `metrics_dir`. They are not user settings.

## R5 — Cache API

**Decision**: `ferro.cache` with an async API mirroring Django's (`get`, `set`, `add`, `delete`,
`get_or_set`, `incr`, `decr`, `get_many`, `set_many`, `delete_many`, `has_key`, `clear`) and two
backends selected by the `cache` setting:

- `memory`: an `OrderedDict` LRU per process (`max_entries` 10,000), storing JSON text so values
  are copied and validated the same way as the database backend.
- `database`: model `ferro.cache.models.CacheEntry` (table `ferro_cache`: `key` primary key,
  `value` text, `expires` float seconds since the epoch, nullable). Upserts use insert-then-update
  on conflict; `add` replaces only expired rows; `incr`/`decr` read the row with
  `select_for_update()` inside a transaction (Feature 013), so concurrent increments are never
  lost; expired rows are removed when read and culled every 100 writes.

`App(cache="database")` imports the model so `makemigrations` creates the table, like the auth
models. Values must be JSON-compatible; anything else raises `TypeError` naming the key.

**Rationale**: JSON keeps the shared store safe to read (pickle from a shared store is code
execution); async matches the ORM; Django's method names are familiar (constitution IX).
