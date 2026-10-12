# Contract: Rust ↔ Python Boundary — native module `ferro._ferro` (internal)

Private to the `ferro` package. Application code never imports `ferro._ferro` directly (only the
`Request` class is re-exported). This contract fixes how many times and how a request crosses the
boundary (Constitution VI).

## Module surface

```python
# ferro/_ferro.pyi (abridged)
__version__: str

def parse_path(path: str) -> list[str]: ...
    # Validates route syntax. Returns parameter names. Raises ValueError with a message naming
    # the problem; Python wraps it in ConfigurationError.

class Server:
    def __init__(
        self,
        routes: list[tuple[list[str], str, Callable[[Request], Awaitable[Any]], str]],
        # (methods, path, endpoint, handler_name)
        settings: dict[str, Any],          # fully resolved, validated in Python and again in Rust
        event_loop: asyncio.AbstractEventLoop,  # Ferro's Python execution loop
    ) -> None: ...                         # builds router; raises ValueError on duplicates
    # Python classes the bridge needs (Response, JSONResponse, HTTPException, ValidationError,
    # asyncio.CancelledError, datetime/date/time, Decimal, UUID) are imported lazily once per
    # interpreter on first use instead of being passed in.
    def start(self) -> tuple[str, int]: ...       # binds + starts engine thread; returns bound addr
    def wait(self, timeout: float | None) -> bool: ...  # releases the interpreter; True when stopped
    def stop(self, graceful: bool = True) -> None: ...

class Request: ...                          # see python-api.md

def log(level: str, target: str, message: str) -> None: ...
def init_logging(level: str) -> None: ...
def json_dumps(value: Any) -> bytes: ...    # same encoder the server uses (tests, JSONResponse)
def json_loads(data: bytes) -> Any: ...
```

## Per-request crossing sequence (bridge v2, research R13)

```text
Engine worker (Rust, never holds the interpreter)   Python execution thread (asyncio loop)
──────────────────────────────────────────────────  ─────────────────────────────────────────
read head+body (limits) → route match
push Job::Start(request, reply) to Rust queue
if no drain pending: call_soon_threadsafe(drain) ──▶ drain(): for each queued job
await reply (with request timeout)                       build Request, endpoint(request)
                                                         eager_task_factory(loop, coro)
                                                         done now?  → convert to response
                                                         suspended? → add_done_callback
                                        ◀────────────── reply.send(FerroResponse)
write response, access log
```

- In steady state a worker makes no interpreter access at all; the only access is the
  `call_soon_threadsafe` wake-up when the queue goes from idle to busy.
- Result conversion (JSON encoding, `Response` attributes, error envelopes) runs on the Python
  thread, which already holds the interpreter; the worker receives finished bytes.
- Request body bytes become a Python `bytes` object lazily, only if `body()`/`json()` is called.
- Timeout or client disconnect → the dropped call queues `Job::Cancel`; the drain cancels the
  asyncio task, which receives `CancelledError`. A job abandoned before it started is skipped.
- The endpoint object is created once per route at registration (no per-request Python
  closures on the Rust side).

## Error mapping performed in Rust

| Python outcome | Response |
|---|---|
| `ValidationError` | 422 envelope + `details` |
| `HTTPException` | its status/message/headers |
| `CancelledError` after timeout | 503 |
| other exception | 500 (debug: type/exception/traceback) and ERROR log with traceback |
| result not convertible | 500 naming handler and type in log (and in debug body) |
