# Caching

```python
from ferro.cache import cache

page = await cache.get_or_set("home", build_home, timeout=60)
```

## Choosing a backend

| Backend | Where values live | Shared by | Use for |
|---|---|---|---|
| `memory` (default) | a dict in each process (least recently used evicted beyond 10,000 entries) | one process | per-process memoization, development |
| `database` | the `ferro_cache` table | every process and server using the database | real caching under `--processes N` or several machines |

```python
app = App(cache="database")      # or FERRO_CACHE=database
```

With `App(cache="database")` the cache table is part of your models, so `ferro makemigrations`
creates it. Choosing it only through `FERRO_CACHE` also works once the table exists.

Scripts and tests configure the cache directly; the server then leaves that choice alone:

```python
from ferro.cache import configure

configure("memory", max_entries=1000, default_timeout=60)
configure("database")
```

## API

All methods are `async`. Timeouts are in seconds: `None` never expires, `0` or less does not
store; the default is 300.

```python
await cache.set("rates", {"usd": 83.1}, timeout=300)
await cache.get("rates")                          # value, or None
await cache.get("rates", default={})
await cache.add("lock:report", True, timeout=30)  # only if absent: True / False
await cache.get_or_set("home", build_home, timeout=60)   # value, callable or async callable
await cache.incr("visits")                        # atomic, also across processes; ValueError if missing
await cache.decr("stock:42", 2)
await cache.has_key("rates")
await cache.delete("rates")
await cache.get_many(["a", "b"])                  # {"a": ...} for present keys
await cache.set_many({"a": 1, "b": 2}, timeout=60)
await cache.delete_many(["a", "b"])
await cache.clear()
```

- **Values** must be JSON-compatible (`dict`, `list`, `str`, numbers, `bool`, `None`) and come
  back as JSON: tuples become lists, `Decimal` and dates become strings. Anything else raises
  `TypeError` naming the key. Values are copies: changing a returned dict never changes the
  cache. (Ferro does not use pickle: unpickling data from a shared store can run code.)
- **Keys** are strings of 1 to 250 characters. Prefix them by purpose (`"user:42:profile"`).
- `get_or_set` runs the factory only on a miss; if two processes miss at once, the first stored
  value wins and both return it.

## Patterns

Cache a page or an expensive query:

```python
@app.get("/stats")
async def stats():
    async def compute():
        return await Order.objects.aggregate(total=Sum("total"), n=Count())
    return await cache.get_or_set("stats", compute, timeout=30)
```

Invalidate on change:

```python
async def save_product(product):
    await product.save()
    await cache.delete(f"product:{product.pk}")
```

A simple lock across processes (with the database backend):

```python
if await cache.add("job:nightly", True, timeout=600):
    try:
        await run_nightly_job()
    finally:
        await cache.delete("job:nightly")
```

## Performance notes

The database backend costs one query per call — far cheaper than the work it saves when that
work is several queries or heavy computation, but not free. For hot values used on every request,
keep a short-lived memory cache in front:

```python
from ferro.cache import MemoryCache

local = MemoryCache(max_entries=1000, default_timeout=5)

async def settings_value():
    return await local.get_or_set("settings", lambda: cache.get("settings"), timeout=5)
```

Expired rows are deleted when read and in batches every 100 writes. Redis and memcached backends
are not available yet.
