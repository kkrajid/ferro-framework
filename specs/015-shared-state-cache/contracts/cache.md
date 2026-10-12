# Contract: `ferro.cache` and shared state

## API (`from ferro.cache import cache`)

| Method | Returns | Notes |
|---|---|---|
| `await get(key, default=None)` | value / default | expired → default |
| `await set(key, value, timeout=DEFAULT_TIMEOUT)` | `None` | replaces; `timeout<=0` deletes |
| `await add(key, value, timeout=...)` | `bool` | only when missing or expired |
| `await get_or_set(key, default, timeout=...)` | value | `default` may be a value, a callable or an async callable; runs on a miss only |
| `await delete(key)` | `bool` | |
| `await has_key(key)` | `bool` | |
| `await incr(key, delta=1)` / `decr` | `int` | `ValueError` if missing or not an integer; atomic |
| `await get_many(keys)` | `dict` | present keys only |
| `await set_many(mapping, timeout=...)` / `delete_many(keys)` | `None` | |
| `await clear()` | `None` | |

- `DEFAULT_TIMEOUT = 300` seconds; `timeout=None` never expires.
- Keys: `str`, 1–250 characters, else `ValueError`.
- Values: JSON-compatible, else `TypeError("cache value for 'k' is not JSON-compatible: ...")`.
- `configure("memory", max_entries=10_000, default_timeout=...)`, `configure("database",
  default_timeout=...)`, `configure(instance)`; unknown name → `ValueError`.
- Setting `cache`: `memory` (default) | `database`; `FERRO_CACHE`; `App(cache=...)`. The server
  applies it at start unless the application called `configure()` itself.
- Table `ferro_cache(key varchar(250) primary key, value text, expires float null, index)`.

## Shared state (internal)

- Supervisor: `tempfile.mkdtemp(prefix="ferro-")` → `rates.shm` (`_ferro.create_shared_state`),
  `metrics/`; workers get `_worker --shared-dir DIR`; removed when the supervisor exits.
- Native settings (internal, not user-facing): `shared_state` (path of the table), `metrics_dir`.
- Metrics snapshot files: `metrics/worker-<pid>-<start_ms>.prom`, first line
  `# ferro-snapshot <unix_ms>`, then the exposition text.
- Merge rules: sum, except `ferro_http_requests_in_flight` (sum over snapshots younger than 5 s),
  `ferro_process_start_time_seconds` (minimum), `ferro_build_info` (maximum).
