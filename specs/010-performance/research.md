# Research: Performance Suite & Multi-Process Serving

**Feature**: [spec.md](spec.md) | **Date**: 2026-10-11

## R1. Sharing one listening socket

- **Decision**: the supervisor binds once with `socket.create_server((host, port),
  backlog=1024)` and hands the socket to every worker: on Windows with `socket.share(pid)`
  (WSADuplicateSocket) written to the worker's stdin, read back with `socket.fromshare`; on
  Linux/macOS the descriptor is inherited (`pass_fds`) and named in `FERRO_WORKER_FD`. The
  worker detaches the raw handle and passes it to `Server.start(listener)`, which adopts it
  (`TcpListener::from_raw_socket` / `from_raw_fd`, the bridge crate's only new `unsafe`) and
  calls `ferro_http::start_on`.
- **Why**: the kernel queues connections on the one socket and each worker accepts from it, so
  a crashed worker loses only its in-flight requests and the port never closes.
  `SO_REUSEPORT` is Linux-only and binds N sockets (a dying worker drops its queued
  connections); Windows has no equivalent.
- **Alternatives rejected**: a Rust front process proxying to workers (an extra hop on every
  request); `multiprocessing` (re-imports `__main__`, pickles the app, awkward on Windows).

## R2. Supervisor

- Workers run `python -m ferro _worker TARGET [server options]` (hidden command) with
  `FERRO_WORKER_ID` and `FERRO_SUPERVISOR_PID`. Each is a normal single-process server: own
  interpreter, engine threads, Python thread, executor and database pool.
- The supervisor imports the app once (validates the target and resolves settings, so a typo
  fails before any worker starts) but never connects the database or serves.
- Monitor loop every 0.2 s: a worker that exits is restarted after a back-off
  (0.5 s × 2^quick failures, at most 10 s); a worker that dies within 5 s of starting counts as
  a quick failure; three in a row stop the supervisor with "worker N failed to start" (exit 1).
- Shutdown (Ctrl+C, SIGTERM, Ctrl+Break): close the socket, send each worker CTRL_BREAK_EVENT
  (Windows; workers have their own process group) or SIGTERM, wait `shutdown_timeout + 5 s`,
  kill leftovers; a second Ctrl+C kills at once. Workers run in their own process group so a
  terminal Ctrl+C reaches only the supervisor (one graceful signal per worker).
- Orphans: a worker thread waits on the supervisor (`WaitForSingleObject` on a SYNCHRONIZE
  handle on Windows, parent-pid polling elsewhere) and interrupts the main thread when it
  disappears, which runs the normal graceful shutdown; if that has not finished
  `shutdown_timeout + 10 s` later the worker exits outright (nobody else would stop it).
- Windows accept quirk (found by the orphan test failing about 1 run in 8): on a listening
  socket shared between processes, a *non-blocking* `accept()` that races with another process
  can block until the next connection arrives (the engine's acceptor thread was seen waiting in
  a Winsock wait instead of its completion port, and a single incoming connection released it).
  The engine's stop request is only seen by an acceptor that is back in its poll loop, so such
  a worker never finished stopping. While a worker with a shared listener waits for its engine
  to stop, it therefore makes a throwaway connection to its own port every 0.25 s
  (`ServerRunner.wake_acceptor`, Windows only). Making the supervisor's socket non-blocking
  before sharing it was tried first and did not help.

## R3. Settings

`processes` (1–64, default 1; `--processes`, `FERRO_PROCESSES`, `App(processes=...)`) is a
Python-only setting. `ferro dev --processes N>1` is an error (auto-reload is single-process);
`App.run()` with more than one process is an error that points to `ferro run --processes`. More
processes than CPUs is allowed with a warning. The banner states "N processes, W workers each".

## R4. What is per process

Everything in memory: rate-limit buckets, caches, the model registry. Sessions, users and data
are in the database, so they work across processes. With SQLite, every worker opens the same
file; writers serialize on SQLite's lock (`BEGIN IMMEDIATE`, busy timeout) as with threads.

## R5. Performance suite

`tests/benchmarks/run_010.py` (oha, release build) prepares one app with SQLite tables, 100
rows and a superuser, then runs per round: the Rust-only baseline, hello world (1 and 4
processes), a JSON route with a path parameter, a database read by primary key (1 and 4
processes), a CPU-heavy sync route (1 and 4 processes), one pass-through middleware and an
authenticated request (session cookie → session + user lookup). Rounds alternate scenarios;
medians of 3 rounds of 10 s after a 3 s warm-up; p50/p99 from oha.

## R6. Profile-driven optimization

`run_010.py --profile` runs the app in-process, enables `cProfile` on the Python execution
thread while oha drives hello world, and prints the top functions. A change is made only for a
function that accounts for ≥ 5% of that thread's time, and is measured before/after.
