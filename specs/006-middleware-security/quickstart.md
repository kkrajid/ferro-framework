# Quickstart & Validation: Middleware & Security Defaults

```python
import time

from ferro import App, CORS, Response

app = App(cors=CORS(allow_origins=["http://localhost:5173"]), rate_limit="100/minute")


@app.middleware
async def timing(request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    response.headers.append(("x-elapsed-ms", f"{(time.perf_counter() - started) * 1000:.1f}"))
    return response


@app.post("/login", rate_limit="5/minute")
async def login(request):
    response = Response({"ok": True})
    response.set_cookie("sid", "abc123", max_age=3600)
    return response
```

```bash
ferro dev
curl -i -X POST http://127.0.0.1:8000/login                                   # 200, set-cookie
curl -i -X POST -H "Origin: https://evil.example" http://127.0.0.1:8000/login  # 403
curl -i -X OPTIONS -H "Origin: http://localhost:5173" \
     -H "Access-Control-Request-Method: POST" http://127.0.0.1:8000/login      # 204 + CORS headers
for i in 1 2 3 4 5 6; do curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/login; done  # ... 429
```

Automated: `pytest tests/python/test_middleware.py tests/python/test_security.py
tests/python/test_rate_limit.py`; `python tests/benchmarks/run_006.py`.
