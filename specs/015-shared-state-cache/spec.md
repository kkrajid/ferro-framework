# Feature Specification: Shared State Across Processes and a Cache API

**Feature Branch**: `015-shared-state-cache`

**Created**: 2026-10-11

**Status**: Draft

**Input**: User description: "Processes thammil shared cache/metrics illa — complete it": with
`ferro run --processes N`, rate limits, metrics and caches are per process today. Make them
behave as one server, and add a cache API.

## Clarifications

### Session 2026-10-11

The user asked to complete these items without interruptions; recommended options were taken.

- Q: How are rate limits shared between worker processes? → A: Through a fixed-size shared
  memory table created by the supervisor for the lifetime of the command. A client's limit is
  the same whichever worker serves it. Single-process servers keep their in-process limiter.
  If the table is full (more active clients than slots), the worker falls back to its own
  limiter for the overflowing clients and says so once in the log.
- Q: How are metrics aggregated? → A: Any worker answering `/metrics` reports totals over all
  workers of the command. Each worker publishes its numbers at least once per second, so other
  workers' numbers may be up to one second old. Counters of a worker that died stay in the totals
  (totals never go backwards); its in-flight gauge does not.
- Q: Which cache backends? → A: `memory` (per process, default, bounded LRU with expiry) and
  `database` (a table through the ORM, shared by every process and machine using the database).
  Values are JSON-compatible (no pickle — loading pickled data from a shared store is unsafe).
- Q: Is the cache API sync or async? → A: Async, like the ORM (`await cache.get(...)`).
- Q: How is the backend chosen? → A: Setting `cache` (`memory` | `database`) through the usual
  precedence (`FERRO_CACHE`, `App(cache=...)`); scripts can call `ferro.cache.configure(...)`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Rate limits hold across processes (Priority: P1)

An operator runs `ferro run --processes 4` with `rate_limit="10/minute"`; a client sending 40
requests must get 10, not 40, whichever worker serves each connection.

**Why this priority**: A limit that multiplies with the process count is a security control that
silently stops working when the operator scales up.

**Independent Test**: 4 processes, limit 10/minute, a client opens a new connection per request;
exactly 10 succeed and the rest get 429 with `Retry-After`.

**Acceptance Scenarios**:

1. **Given** N processes and a global limit, **Then** a client gets exactly the limit, not N times it.
2. **Given** per-route limits, **Then** they are shared the same way.
3. **Given** one process, **Then** behaviour is unchanged.
4. **Given** a worker restart, **Then** the limits already used are kept.

---

### User Story 2 - Metrics for the whole server (Priority: P1)

An operator scrapes `/metrics` from a 4-process server and needs request counts for all of them,
not a random quarter.

**Why this priority**: Per-process metrics make dashboards wrong by a factor of N.

**Independent Test**: 4 processes, 400 requests, then scrape: `ferro_http_requests_total` sums to
400 (+ the scrapes' own counts) within 2 seconds, from whichever worker answers.

**Acceptance Scenarios**:

1. **Given** N processes, **Then** request counters, duration histograms and database query
   counters are totals over all workers; in-flight is the sum of the live workers.
2. **Given** a worker that died and was replaced, **Then** totals never decrease.
3. **Given** one process, **Then** metrics are as before (always current).

---

### User Story 3 - A cache API (Priority: P2)

A developer caches expensive results (`await cache.get_or_set("home", build, timeout=60)`), and
with the database backend the cache is shared between processes and servers.

**Why this priority**: Every web framework user expects a cache; per-process module globals are
wrong under `--processes`.

**Independent Test**: With `cache="database"`, a value set by one process is read by another;
expiry, delete, `incr` and `clear` work; on SQLite and PostgreSQL.

**Acceptance Scenarios**:

1. **Given** `await cache.set("k", {"a": 1}, timeout=1)`, **Then** `get` returns it, and `None`
   (or the default) after it expires.
2. **Given** `get_or_set(key, factory, timeout)`, **Then** the factory (sync or async) runs only on
   a miss.
3. **Given** concurrent `incr` calls on the database backend, **Then** no increment is lost.
4. **Given** a non-JSON value (an object, bytes), **Then** `TypeError` naming the key.
5. **Given** the memory backend and more entries than its limit, **Then** the least recently used
   are evicted.
6. **Given** the database backend, **Then** `makemigrations` creates its table (`ferro_cache`).

### Edge Cases

- Keys: non-empty strings up to 250 characters; others → `ValueError`.
- `timeout=None` means no expiry; `timeout=0` means do not store.
- The shared memory file lives in a temporary directory removed when the supervisor exits.

## Requirements *(mandatory)*

- **FR-001**: With `--processes N`, global and per-route rate limits are enforced across all
  workers with the same semantics as one process (token bucket of `count` per `period`).
- **FR-002**: The shared limiter is lock-free, bounded in memory, keyed with a per-run secret
  (clients cannot choose colliding keys), and falls back to the process limiter when full.
- **FR-003**: With `--processes N`, `/metrics` from any worker reports totals across workers per
  the clarification; series and names are unchanged.
- **FR-004**: `ferro.cache`: `get`, `set`, `add`, `delete`, `get_or_set`, `incr`, `decr`,
  `get_many`, `set_many`, `delete_many`, `clear`, `has_key`; async; JSON values; key validation.
- **FR-005**: Backends `memory` (`max_entries`, default 10,000) and `database` (ORM model
  `CacheEntry`, table `ferro_cache`); `cache` setting and `ferro.cache.configure()`.
- **FR-006**: Database backend works on SQLite and PostgreSQL; `incr` is atomic (row lock).
- **FR-007**: Documentation explains what is shared, staleness of metrics and cache choices.

## Success Criteria *(mandatory)*

- **SC-001**: 4 processes, limit 10/minute, 40 requests on fresh connections → exactly 10 × 200.
- **SC-002**: 4 processes, 400 requests → scraped total ≥ 400 within 2 s and never decreasing over
  a worker restart.
- **SC-003**: Database cache: a value set in one process is read in another; 50 concurrent `incr`
  calls produce exactly +50 on both databases.

## Assumptions

- Redis/memcached backends, per-view caching decorators and cache versioning are out of scope.
- Rate limiting across machines remains per machine (use a gateway for global limits).
