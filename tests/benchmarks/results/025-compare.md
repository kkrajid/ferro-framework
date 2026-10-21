# Feature 025: Ferro vs FastAPI vs Flask

Measured 2026-10-21 08:08 UTC on Windows-11-10.0.26200-SP0, 8 logical CPUs, CPython 3.13.12; oha 50 connections, 10 s per run after a warm-up, 3 rounds (servers alternating); medians. Client and servers share the machine.

Ferro 0.5.0rc2 (release build, `ferro run --workers 4`, 1 process); FastAPI 0.156.5 on uvicorn 0.54.0 (1 worker, httptools); Flask 3.1.3 on waitress 3.0.2 (8 threads). Database scenario: Ferro's ORM; FastAPI and Flask with `sqlite3` directly (no ORM).

| Scenario | Server | req/s | p50 ms | p99 ms | failed | vs FastAPI |
|---|---|---|---|---|---|---|
| hello world (JSON) | Ferro | 37,683 | 1.09 | 3.94 | 0 | 13.06x |
| hello world (JSON) | FastAPI (uvicorn) | 2,886 | 15.99 | 37.79 | 0 | 1.00x |
| hello world (JSON) | Flask (waitress) | 1,673 | 27.04 | 65.19 | 0 | 0.58x |
| path + query parameters | Ferro | 31,167 | 1.20 | 6.73 | 0 | 14.76x |
| path + query parameters | FastAPI (uvicorn) | 2,112 | 22.48 | 48.32 | 0 | 1.00x |
| path + query parameters | Flask (waitress) | 2,015 | 22.58 | 50.62 | 0 | 0.95x |
| JSON body validated by Pydantic | Ferro | 19,896 | 2.32 | 4.76 | 0 | 10.17x |
| JSON body validated by Pydantic | FastAPI (uvicorn) | 1,957 | 24.39 | 50.35 | 0 | 1.00x |
| JSON body validated by Pydantic | Flask (waitress) | 1,678 | 27.05 | 64.50 | 0 | 0.86x |
| SQLite read by primary key | Ferro | 14,020 | 3.42 | 6.68 | 0 | 10.69x |
| SQLite read by primary key | FastAPI (uvicorn) | 1,312 | 35.90 | 72.96 | 0 | 1.00x |
| SQLite read by primary key | Flask (waitress) | 1,656 | 26.53 | 63.24 | 0 | 1.26x |

## Four processes each (same machine, 2 rounds, `scratchpad` check)

| Scenario | Server | req/s | p50 ms |
|---|---|---|---|
| hello world | Ferro (`--processes 4 --workers 2`) | 10,708 | 1.76 |
| hello world | FastAPI (`uvicorn --workers 4`, httptools) | 2,349 | 18.52 |
| SQLite read | Ferro (`--processes 4 --workers 2`) | 4,010 | 8.96 |
| SQLite read | FastAPI (`uvicorn --workers 4`, httptools) | 1,174 | 20.36 |

## Reading these numbers

- Windows 11 with about 0.8 GB of free memory; client and servers on the same 8 CPUs. On
  Windows uvicorn has no uvloop and runs one event-loop thread per worker; Ferro does its HTTP
  work in Rust threads and hands only the handler to Python. On Linux with uvloop FastAPI is
  usually several times faster than here, so expect a smaller gap there — measure on your
  deployment platform.
- On this machine more processes did not help either framework (Ferro's own Feature 010 result
  for Windows); `--processes` pays off for CPU-bound handlers.
- The database rows compare Ferro's ORM with raw `sqlite3` in the others. The PostgreSQL
  checkout transaction measured in Feature 020 (Ferro behind asyncpg on per-query latency) is
  not part of this comparison.
