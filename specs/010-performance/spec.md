# Feature Specification: Performance Suite & Multi-Process Serving

**Feature Branch**: `010-performance`

**Created**: 2026-10-11

**Status**: Draft

**Input**: User description: "Feature 010 from docs/roadmap.md (vision Phase 9, §20): benchmarks
versus the raw Rust baseline, boundary/serialization optimization; plus the decision recorded in
Feature 001: worker threads now, worker processes later (Feature 010)."

## Clarifications

### Session 2026-10-11

The user asked to continue without interruptions; recommended options were taken and recorded.

- Q: How does Ferro use more than one CPU for Python code? → A: `ferro run --processes N`
  (`FERRO_PROCESSES`, default 1): a supervisor binds the port once and starts N worker
  processes sharing the listening socket; each has its own interpreter, engine threads and
  database pool. Crashed workers are restarted (with a back-off); Ctrl+C / SIGTERM stops all of
  them gracefully. `ferro dev` stays single-process (auto-reload).
- Q: How is the socket shared on Windows? → A: With Windows socket duplication handed to each
  worker over a pipe; on Linux/macOS the descriptor is inherited. Workers adopt the socket
  through one native entry point.
- Q: What does the performance suite measure? → A: One command that runs, in the same session:
  the Rust-only baseline, hello world with 1 and N processes, a JSON route with a path
  parameter, a database read route, a CPU-heavy Python route, and the cost of middleware and
  authentication; it writes one results table with ratios to the baseline.
- Q: Which boundary optimizations are in scope? → A: Only those a profile of the hello-world
  path shows to be worth at least 5%, each measured before and after; none are made on guesses.
- Q: What is shared between worker processes? → A: Nothing in memory: rate limits and caches
  are per process (documented); sessions and data live in the database, so they work across
  processes.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Multi-process serving (Priority: P1)

```bash
ferro run --processes 4
```

**Why this priority**: Python handlers share one interpreter per process; using all CPUs needs
processes.

**Acceptance Scenarios**:

1. **Given** `--processes 3`, **Then** three workers serve the same port, concurrent requests are
   answered by more than one worker, and the banner states the process count.
2. **Given** a worker that crashes, **Then** it is replaced and the server keeps answering.
3. **Given** Ctrl+C / SIGTERM, **Then** every worker shuts down gracefully and the command exits 0.
4. **Given** the port is in use, **Then** one clear error and exit 3 (no worker storm).
5. **Given** a database URL, **Then** every worker connects its own pool; sessions created by one
   worker are valid in the others.
6. **Given** `ferro dev --processes 2`, **Then** a clear error (development mode is single-process).

---

### User Story 2 - Performance suite (Priority: P1)

**Acceptance Scenarios**:

1. **Given** `python tests/benchmarks/run_010.py`, **Then** it measures every scenario above in one
   session (alternating, medians of repeated runs) and writes
   `tests/benchmarks/results/010-performance.md`.
2. **Given** the results, **Then** each scenario reports requests per second, p50/p99 latency,
   failures, and the ratio to the Rust-only baseline.

---

### User Story 3 - Measured optimizations (Priority: P3)

**Acceptance Scenarios**:

1. **Given** a profile of the hello-world path, **Then** any change made is justified by it and
   shows its before/after numbers in the results; behaviour and all tests are unchanged.

### Edge Cases

- A worker that fails at startup (import error) makes the supervisor stop with that error
  instead of restarting forever.
- Workers exit if the supervisor dies (no orphans).
- `--processes` larger than the CPU count is allowed but warned about.

## Requirements *(mandatory)*

- **FR-001**: `processes` setting (CLI `--processes`, `FERRO_PROCESSES`, `App(processes=...)`),
  1–64, for `ferro run` / `App.run()`.
- **FR-002**: Supervisor: one listening socket, N workers, restart on crash with back-off, startup
  failure detection, graceful shutdown, orphan protection.
- **FR-003**: Native adoption of an inherited/duplicated listening socket.
- **FR-004**: `run_010.py` performance suite and results file.
- **FR-005**: Profile-driven optimizations only, each measured.

## Success Criteria *(mandatory)*

- **SC-001**: With 4 processes, a CPU-heavy Python route serves at least 2.5× the requests per
  second of 1 process (8 logical CPUs, client on the same machine).
- **SC-002**: With 4 processes, hello world serves at least 1.3× the requests per second of
  1 process.
- **SC-003**: 0 failed requests across the suite; workers killed during a load run are replaced
  with fewer than 1% failed requests.

## Assumptions

- The benchmark client runs on the same machine (as in Feature 001), so absolute numbers are
  conservative.
- Shared-memory caches between processes are out of scope.
