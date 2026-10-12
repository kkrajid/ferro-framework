# Data Model: Production Hardening

**Feature**: [spec.md](spec.md) | **Research**: [research.md](research.md)

No database tables. The entities are runtime state in the engine and the bridge.

## Probe result

| Field | Type | Rules |
|---|---|---|
| status | `ok` (liveness) · `ready` / `not ready` (readiness) | readiness is `ready` iff `failing` is empty |
| failing | list of check names | subset of `server`, `database`, in that order; empty for liveness |

Wire format: [contracts/http-probes.md](contracts/http-probes.md).

## Readiness cache (bridge, per process)

| Field | Type | Rules |
|---|---|---|
| checked_at | instant or none | answer reused while younger than 1 s |
| healthy | bool | result of the last `SELECT 1` (750 ms limit) |
| running | bool | at most one check at a time; other probes wait ≤ 0.85 s for it |

Transitions: `stale → running → fresh(healthy | unhealthy) → stale (after 1 s)`. A check that
does not answer within 750 ms, or a backend error of any kind, counts as unhealthy. No database
configured → the check is skipped (never fails).

## Server lifecycle state (engine, per process)

| State | Readiness | Accepting connections | Entered by |
|---|---|---|---|
| serving | `ready` (if database healthy) | yes | start |
| draining | `not ready` (`server`) | yes | `begin_drain()` (first stop signal with `shutdown_drain` > 0) |
| stopping | `not ready` (`server`) | no | `stop(graceful=True)` |
| stopped | — | no | engine finished (in-flight done or cut off after `shutdown_timeout`) |

A second stop signal in `draining` or `stopping` calls `stop(graceful=False)`.

## Shutdown statistics (engine)

| Field | Meaning |
|---|---|
| in_flight | requests being handled now (also the `ferro_http_requests_in_flight` gauge) |
| at_stop | `in_flight` when `stop` was called |
| finished | requests that completed after `stop` was called |
| cut_off | requests whose handling was dropped unfinished after `stop` was called |

Logged once when the server thread ends: see [contracts/logging.md](contracts/logging.md).

## Metric registry (engine, per process; only when `metrics` is on)

| Series | Key | Storage |
|---|---|---|
| requests | (route slot, method, status class) | counter |
| request duration | (route slot, method) | histogram: 13 buckets + sum + count |
| in flight | — | gauge (shared with shutdown statistics) |
| database queries | outcome ∈ {ok, error, unavailable} | counter (bridge) |
| database query duration | — | histogram (bridge) |

Route slot = route id (label: the route's path template) or one extra slot `<unmatched>`
(404, 405 and redirects to unknown paths). Method ∈ {GET, HEAD, POST, PUT, PATCH, DELETE,
OPTIONS, other}. Status class ∈ {1xx, 2xx, 3xx, 4xx, 5xx}. All sizes fixed at start.
Names and format: [contracts/metrics.md](contracts/metrics.md).

## Database errors

| `DbError` variant | Python exception | Unhandled in a handler → |
|---|---|---|
| Integrity | `IntegrityError` (or `ProtectedError` on delete) | 500 |
| Operational | `OperationalError` | 500 |
| MissingTable | `OperationalError` (with the create-tables hint) | 500 |
| **Unavailable** (new) | **`DatabaseUnavailableError`** (subclass of `OperationalError`) | **503 + `Retry-After: 1`** |
| Closed | `DatabaseUnavailableError` (was `OperationalError`) | **503 + `Retry-After: 1`** |
| Other | `DatabaseError` | 500 |

`Unavailable` sources: PostgreSQL cannot connect / connection lost / communication error /
SQLSTATE class `08`, `53300`, `57P01`–`57P03`, `55P03`; SQLite busy or locked after the busy
timeout; either backend's job queue full.

## Log record (Python)

`logging.LogRecord` gains `request_id: str | None` (set by Ferro's record factory for every
record in the process). JSON output schema: [contracts/logging.md](contracts/logging.md).

## Settings (new)

See [contracts/settings.md](contracts/settings.md).
