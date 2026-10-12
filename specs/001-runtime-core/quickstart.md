# Quickstart & Validation: Runtime Core & Python Handlers

Runnable scenarios proving Feature 001 end to end. Contracts:
[python-api](contracts/python-api.md), [cli](contracts/cli.md),
[http-behavior](contracts/http-behavior.md).

## Prerequisites

- Rust stable (`cargo --version`), on Windows the MSVC C++ build tools
- `uv` and CPython ≥ 3.12
- Optional for load scenarios: `cargo install oha`

## Build (development install)

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv maturin pytest httpx ruff
uv run --python .venv maturin develop --uv
```

## Automated gates

```bash
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
uv run --python .venv ruff check python tests examples
uv run --python .venv pytest
```

Expected: all pass.

## Scenario 1 — Hello world (US1, SC-001)

```bash
cd examples/hello_world
ferro dev
curl -i http://127.0.0.1:8000/
```

Expect `200`, `content-type: application/json`, body `{"message":"Hello from Rust"}`, plus
`x-request-id`, `x-content-type-options: nosniff`, no `server` header. `curl -i -X POST
http://127.0.0.1:8000/` → `405` with `allow: GET, HEAD`. `/missing` → `404` envelope. Ctrl+C →
exit code 0.

## Scenario 2 — Parameters (US2)

```bash
curl "http://127.0.0.1:8000/products/42?currency=USD"   # {"id":42,"currency":"USD","path":"/products/42"}
curl -i http://127.0.0.1:8000/products/abc              # 422, details[0].param == "id"
curl -i http://127.0.0.1:8000/products/42/              # 308, location: /products/42
curl http://127.0.0.1:8000/users/me                     # static route wins over /users/{id}
```

## Scenario 3 — JSON API (US3)

```bash
curl -i -X POST -H "content-type: application/json" -d '{"name":"Laptop"}' http://127.0.0.1:8000/products
# 201, location: /products/1, body echoes the JSON
curl -i -X POST -H "content-type: application/json" -d '{bad' http://127.0.0.1:8000/products   # 400
```

Oversized body (2 MiB) → `413`.

## Scenario 4 — Errors (US4, SC-006)

```bash
curl -i http://127.0.0.1:8000/orders/7        # 404 {"error":{"status":404,"message":"Order not found",...}}
curl -i http://127.0.0.1:8000/boom            # dev: 500 with type/traceback; run: 500 generic only
```

Under `ferro run`, the `/boom` body contains only `status`, `message`, `request_id`; the server
log contains the traceback with the same request id.

## Scenario 5 — Commands & config (US5, SC-005, SC-007)

```bash
ferro routes                                   # table, no server started
FERRO_PORT=9001 ferro run --port 9000          # listens on 9000 (CLI beats env)
ferro dev  # then edit app.py → new code served within 2 s; introduce a syntax error → error shown,
           # fix it → server resumes
ferro dev nonexistent:app                      # exit 2, "error: could not import module 'nonexistent'"
```

Graceful shutdown: start 50 requests to a 2-second handler, send SIGTERM (Ctrl+C on Windows)
after 0.5 s → all 50 complete with 200, process exits 0.

## Scenario 6 — Sync isolation & load (US6, SC-002–SC-004)

```bash
ferro run --workers 4 &
oha -z 30s -c 100 http://127.0.0.1:8000/                 # SC-002: ≥ 10,000 req/s, 0 errors
oha -z 60s -c 1000 http://127.0.0.1:8000/                # SC-003: 0 errors, memory ≤ 2× idle
oha -z 20s -c 20 http://127.0.0.1:8000/sync-sleep &      # sync handler sleeping 1 s
oha -z 20s -c 10 http://127.0.0.1:8000/                  # SC-004: p99 < 50 ms
```

Record results in `tests/benchmarks/results/001-runtime-core.md` with machine details.
