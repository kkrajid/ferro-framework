# Feature Specification: Production Hardening

**Feature Branch**: `011-production-hardening`

**Created**: 2026-10-11

**Status**: Draft

**Input**: User description: "Feature 011 from docs/roadmap.md (vision Phase 10, \"Stability &
production hardening — stress, failure and shutdown tests pass\"; §19 testing strategy:
concurrency, connection exhaustion, DB latency, large result sets, large bodies): health and
readiness endpoints, metrics, request IDs in logs, graceful shutdown that drains traffic, and a
permanent stress/failure/shutdown test suite. Request IDs and graceful shutdown exist since
Feature 001; this feature makes them production-grade and proves behaviour under stress and
failure."

## Clarifications

### Session 2026-10-11

The user asked to continue without interruptions; recommended options were taken and recorded.

- Q: Are the probes on by default, and where? → A: On by default on the application's own port,
  at `/healthz` (liveness) and `/readyz` (readiness); `health_path` / `ready_path` settings move
  them, an empty value disables one. They bypass application middleware, rate limits and CSRF,
  and are logged only at debug level.
- Q: What does readiness check? → A: The server state (not draining or stopping) and, when a
  database is configured, a trivial query with a 750 ms limit whose result is reused for 1
  second (one check at a time). Application-defined checks are out of scope for this feature.
- Q: How are metrics exposed and protected? → A: Off by default (`metrics` setting); when on, the
  plain-text exposition format at `/metrics` (`metrics_path`), answered only to client addresses
  in `metrics_allow` (default: loopback), judged after trusted-proxy resolution; others get "not
  found". Metrics are per process.
- Q: How do request ids reach application logs, and in which format? → A: The id of the request
  being handled is attached automatically to every record from Python's `logging` (async and
  sync handlers), so developers' own log formats can show it; when the application has not
  configured logging itself, its records go through Ferro's log output at Ferro's log level.
  `log_format` = `text` (default) or `json` (one record per line, all sources).
- Q: How is the stress suite run? → A: As a permanent pytest suite in `tests/stress/` that runs
  by default in a reduced size (under 2 minutes) and in full size with `FERRO_STRESS=full`, which
  also writes a results file; database-down scenarios use the PostgreSQL test cluster.

### Session 2026-10-11 (after surveying current behaviour)

- Q: Which status does a request get when the database is unreachable? → A: 503 "service
  unavailable" with `Retry-After` (today an unhandled connection error is a 500). Applies to
  cannot connect, connection lost, database closed and lock wait expired; developers can catch
  these as one distinct error, which remains an operational error for existing code. Other
  database errors keep their current statuses.
- Q: What happens when more database work arrives than the database can queue? → A: It fails at
  once with the same unavailable error (503) instead of making the application thread wait,
  so requests that do not use the database keep being served.
- Q: Is a slow request body bounded? → A: Yes: the request timeout covers receiving the body
  too; a client that has not finished sending it in time gets 408 "request timeout" and the
  connection is closed (today only headers are bounded). Handler time keeps its existing
  timeout response (503, Feature 001).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Health and readiness probes (Priority: P1)

An operator deploys a Ferro application behind a load balancer or an orchestrator. The platform
asks the application two questions every few seconds: "is the process alive?" and "should it
receive traffic now?". The application answers both without the developer writing any code, and
the readiness answer turns negative while the database is unreachable or the server is shutting
down, so traffic moves elsewhere.

**Why this priority**: Without probes, platforms route traffic to instances that cannot serve it
(database down, shutting down) and cannot restart hung instances; this is the first thing every
production deployment needs.

**Independent Test**: Start an application with a database, query the liveness and readiness
addresses, stop the database, query again, restart the database, query again.

**Acceptance Scenarios**:

1. **Given** a running application, **When** the liveness address is requested, **Then** it
   answers success at once, without running any application code.
2. **Given** a running application whose database answers, **When** the readiness address is
   requested, **Then** it answers success.
3. **Given** the database becomes unreachable, **When** the readiness address is requested,
   **Then** it answers "not ready" (service unavailable) naming the failing check, within 2
   seconds, while liveness still succeeds.
4. **Given** the database comes back, **Then** readiness succeeds again without restarting the
   application.
5. **Given** an application route that uses the same address as a probe, **Then** startup fails
   with a clear message naming the route and the setting that moves or disables the probe.
6. **Given** a flood of readiness requests, **Then** the database is checked at most once per
   second regardless of request volume.

---

### User Story 2 - Graceful shutdown that drains traffic (Priority: P1)

The platform asks the application to stop (deployment, scale-down). Requests already being
handled finish; the platform has time to stop sending new traffic; the process then exits
within a bounded time, even if some requests never finish.

**Why this priority**: Every deployment restarts instances; dropped requests during deploys are
the most visible production failure.

**Independent Test**: Run continuous load, send the stop signal, verify no in-flight request
fails, readiness turns negative during the drain period, and the process exits within the
configured bound.

**Acceptance Scenarios**:

1. **Given** a drain period is configured, **When** the stop signal arrives, **Then** readiness
   answers "not ready" immediately, while ordinary requests are still served for the drain period.
2. **Given** the drain period has passed, **Then** the server stops accepting new connections,
   lets in-flight requests finish within the shutdown timeout, and exits with code 0.
3. **Given** requests that exceed the shutdown timeout, **Then** they are cut off, the process
   exits within the shutdown timeout plus one second, and the log names how many requests were
   cut off.
4. **Given** a second stop signal during the drain or shutdown, **Then** the process stops at once.
5. **Given** multi-process serving (Feature 010), **Then** every worker drains the same way.

---

### User Story 3 - Predictable behaviour under stress and failure (Priority: P1)

Under overload and partial failure, a Ferro application answers every request with a correct,
meaningful status, keeps serving what it still can, leaks nothing, and recovers by itself. The
Ferro team (and anyone evaluating Ferro) can re-run one permanent suite that proves it against a
real server.

**Why this priority**: The vision's exit criterion for this phase is "stress, failure and shutdown
tests pass"; claims of stability need repeatable proof.

**Independent Test**: Run the suite; every scenario reports pass/fail with numbers.

**Acceptance Scenarios**:

1. **Given** many concurrent clients on mixed routes (plain, database, failing), **Then** every
   request gets a correct answer (2xx for good routes, a 500 error envelope for failing ones),
   and memory after the run is within 10% of memory after warm-up.
2. **Given** more simultaneous connections than the connection limit, **Then** the excess waits
   and is served as others finish, without crashing, and normal service resumes once load
   drops.
3. **Given** a database that answers slowly, **Then** requests that exceed the request timeout
   get the existing timeout response (service unavailable) on time, their handlers are
   cancelled, and every database connection is free again once the slow queries end.
4. **Given** the database goes down during load, **Then** requests that need it answer
   "service unavailable" (not an internal error) with no internal details, requests that do
   not need it keep succeeding, and service recovers when the database returns, without a
   restart.
5. **Given** more database work than the database can queue, **Then** the excess fails at once
   with "service unavailable" while requests that do not use the database stay fast.
6. **Given** request bodies above the size limit, **Then** they are refused quickly without the
   server reading them completely; bodies at the limit succeed.
7. **Given** a client that sends its body very slowly, **Then** it gets "request timeout" once
   the request timeout passes and its connection is closed.
8. **Given** a query returning a large result set, **Then** it completes and memory returns to
   its previous level afterwards.
9. **Given** clients that disconnect while their request runs, **Then** the handler is
   cancelled, any open transaction is rolled back, and no connection leaks.
10. **Given** clients that send headers very slowly, **Then** they are disconnected after the
    header timeout while other clients are served normally.

---

### User Story 4 - Request IDs in application logs (Priority: P2)

A developer investigating a failed request takes the request id from the response (or the error
body) and finds every log line produced while handling that request — including lines written by
their own handler code — and can ship the logs as structured lines to a log system.

**Why this priority**: Request ids already reach responses, error bodies and access logs; without
them in application logs, the developer's own diagnostics cannot be correlated.

**Independent Test**: A handler logs a message; the log line carries the same request id as the
response header; with structured logging enabled, every line is one parseable record.

**Acceptance Scenarios**:

1. **Given** a handler (async or sync) that writes to the standard logging module with its own
   logger, **Then** the line includes the request id of the request being handled.
2. **Given** log lines written outside any request, **Then** they carry no request id (and do not
   fail).
3. **Given** an application that configures logging itself, **Then** Ferro leaves its handlers
   alone and its formats can include the request id.
4. **Given** structured log output is selected, **Then** every log line (access, errors,
   application) is one machine-readable record with timestamp, level, logger, message and request
   id when there is one.

---

### User Story 5 - Metrics (Priority: P2)

An operator enables metrics and points a monitoring system at the application. They see request
counts by route, method and status, latency distribution, requests in flight, and database query
counts, errors and latency.

**Why this priority**: Probes say whether an instance works; metrics say how well. Useful but not
required to run safely, hence after the probes.

**Independent Test**: Enable metrics, send known requests, read the metrics address and check the
counts.

**Acceptance Scenarios**:

1. **Given** metrics are not enabled (default), **Then** no metrics address exists.
2. **Given** metrics are enabled, **When** requests are made, **Then** the metrics show their
   counts by route template (not raw path), method and status, and latency distributions.
3. **Given** requests to unknown paths, **Then** they are counted under one shared label, so
   arbitrary paths cannot create unbounded metric series.
4. **Given** metrics are enabled, **When** a client outside the allowed addresses requests them,
   **Then** it is refused (not found).
5. **Given** metrics are enabled, **Then** hello-world throughput drops by at most 5%.

### Edge Cases

- Probe addresses requested with methods other than GET/HEAD answer "method not allowed".
- Probes and metrics are not subject to rate limits, CSRF or application middleware, and do not
  appear in access logs at the default log level (they would drown real traffic).
- Readiness without a configured database checks only the server state.
- A database check that hangs counts as failed after 750 ms.
- The drain period is 0 by default (current behaviour); a second stop signal skips it.
- Multi-process: each worker answers probes and metrics for itself (metrics are per process, as
  decided in Feature 010).
- Development mode: probes work as in production; metrics follow the setting.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The server MUST answer a liveness address (default `/healthz`) and a readiness
  address (default `/readyz`) without running application code; both MUST be movable or
  disableable by setting.
- **FR-002**: Readiness MUST fail while the server is draining or stopping, and while the
  configured database does not answer a trivial query within 750 ms; the database check MUST
  run at most once per second however many probes arrive.
- **FR-003**: Probe responses MUST contain only the status and the names of failing checks — no
  versions, addresses, database URLs or error text.
- **FR-004**: An application route on a probe or metrics address MUST be a startup error naming the
  route and the setting.
- **FR-005**: A drain period setting (default 0 s) MUST keep serving with failing readiness for
  that long after a stop signal, before connections stop being accepted; the existing shutdown
  timeout then bounds in-flight work; a second signal MUST stop at once.
- **FR-006**: Shutdown MUST log how many in-flight requests were completed and how many were cut
  off.
- **FR-007**: Log records written by application code during a request MUST include that
  request's id, for async handlers, synchronous handlers and code they call; when the
  application has not configured logging, its records MUST be written through Ferro's log
  output; when it has, Ferro MUST NOT replace its configuration.
- **FR-008**: A log format setting MUST offer human-readable text (default) and structured
  one-record-per-line output covering all log sources.
- **FR-009**: Metrics MUST be off by default; when enabled they MUST be served at a configurable
  address in the standard text exposition format, only to allowed client addresses (default:
  the local machine), and MUST include: requests by route template/method/status class, request
  duration distribution, requests in flight, database queries by outcome and their duration.
- **FR-010**: Metric label values MUST be bounded: unknown paths share one label; no label may
  contain user-supplied text.
- **FR-011**: A permanent stress/failure suite MUST cover the scenarios of User Story 3 against a
  real server and both databases where a database is involved, report numbers, and run in a
  reduced form (under 2 minutes) by default and in full form on request.
- **FR-012**: All new settings MUST follow existing precedence (command line > environment >
  `App(...)` > default) and validation with clear errors.
- **FR-013**: A database connectivity failure (cannot connect, connection lost, database
  closed, lock wait expired) that a handler does not handle MUST produce a 503 error envelope
  with `Retry-After`, never an internal error; developers MUST be able to catch these failures
  as one distinct error type that existing handlers of operational errors still catch.
- **FR-014**: Database work that cannot be queued MUST fail immediately with that unavailable
  error instead of blocking the application's execution thread.
- **FR-015**: Receiving a request body MUST be bounded by the request timeout; on expiry the
  client gets 408 and the connection is closed.

### Key Entities

- **Probe result**: overall status (ready / not ready) and the list of failing checks (server
  state, database).
- **Metric series**: name, bounded labels (route template, method, status class, outcome), value
  or distribution buckets; one set per process.
- **Log record**: time, level, logger, message, request id (optional), plus source-specific fields.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Readiness reports a database outage within 2 seconds and recovery within 2 seconds,
  with liveness succeeding throughout.
- **SC-002**: With 100 clients sending continuous requests, a stop signal with a 2-second drain
  period produces 0 failed requests among those started before connections stop being accepted,
  and the process exits within drain period + shutdown timeout + 1 second.
- **SC-003**: The stress suite completes with 0 crashes, 0 wrong answers, 0 leaked database
  connections, and memory after load within 10% of memory after warm-up.
- **SC-004**: 100% of application log lines written during requests carry the matching request id.
- **SC-005**: Enabling metrics costs at most 5% of hello-world throughput (same-session
  comparison).

## Assumptions

- Probe defaults follow common orchestrator conventions (`/healthz`, `/readyz`) so most
  deployments need no configuration.
- Metrics use the widely supported plain-text exposition format; no client library is required
  in the application.
- The stress suite runs on the development machine (Windows 11, 8 logical CPUs) with the client on
  the same machine; numbers are relative to that machine.
- Stopping and restarting the database during tests uses the temporary PostgreSQL cluster the test
  suite already manages (Feature 003); SQLite has no "down" state and is covered by the slow-query
  scenario.
- Request ids keep their existing rules from Feature 001 (accept a well-formed incoming id,
  otherwise generate one).
