# Feature 026: memory of Ferro vs FastAPI vs Flask

Measured 2026-10-21 16:12 UTC on Windows-11-10.0.26200-SP0 (AMD64 Family 23 Model 24 Stepping 1, AuthenticAMD, 8 logical CPUs), CPython 3.13.12; resident memory (RSS) of each server's process tree, sampled every 100 ms; oha 50 connections, 10 s per scenario; medians of 2 rounds. Client and servers share the machine.

Ferro 0.6.0rc2 (release build, `ferro run --workers 4`, 1 process); FastAPI 0.156.5 on uvicorn 0.54.0 (1 worker, httptools); Flask 3.1.3 on waitress 3.0.2 (8 threads). Every app imports Pydantic.

| Server | Idle MiB | Peak MiB | After load MiB | Growth under load |
|---|---|---|---|---|
| Ferro | 46.1 | 49.2 | 48.8 | +3.1 MiB |
| FastAPI (uvicorn) | 59.3 | 75.9 | 69.7 | +16.6 MiB |
| Flask (waitress) | 54.7 | 55.6 | 55.6 | +0.9 MiB |

Peak memory and throughput per scenario:

| Scenario | Server | Peak MiB | req/s |
|---|---|---|---|
| hello world (JSON) | Ferro | 47.8 | 25,208 |
| hello world (JSON) | FastAPI (uvicorn) | 62.4 | 2,685 |
| hello world (JSON) | Flask (waitress) | 54.7 | 1,965 |
| path + query parameters | Ferro | 48.2 | 24,585 |
| path + query parameters | FastAPI (uvicorn) | 65.6 | 2,203 |
| path + query parameters | Flask (waitress) | 54.8 | 1,697 |
| JSON body validated by Pydantic | Ferro | 48.6 | 16,410 |
| JSON body validated by Pydantic | FastAPI (uvicorn) | 68.7 | 2,175 |
| JSON body validated by Pydantic | Flask (waitress) | 54.8 | 1,565 |
| SQLite read by primary key | Ferro | 49.2 | 12,458 |
| SQLite read by primary key | FastAPI (uvicorn) | 75.9 | 1,525 |
| SQLite read by primary key | Flask (waitress) | 55.6 | 1,514 |
