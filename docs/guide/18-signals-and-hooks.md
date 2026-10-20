# Signals, on_commit and lifecycle hooks

## Lifecycle hooks

```python
import httpx

from ferro import App

app = App()


@app.on_startup
async def open_clients():
    app.http = httpx.AsyncClient(timeout=5)


@app.on_shutdown
async def close_clients():
    await app.http.aclose()
```

- `on_startup` functions (sync or `async`, no arguments) run on the server's event loop before
  it accepts requests, in registration order; an exception stops the server from starting.
- `on_shutdown` functions run after the server stopped serving, in reverse order; exceptions
  are logged and the others still run (within `shutdown_timeout`).
- Both run in every process of `ferro run --processes N`, and in `TestClient`.

## After the commit: `on_commit`

Side effects that must only follow committed data — e-mails, background jobs, cache purges,
webhooks — go in `on_commit`:

```python
from ferro.db import on_commit, transaction

async with transaction():
    order = await Order.objects.create(customer=customer, total=total)
    await on_commit(lambda: send_confirmation(order.pk))      # sync or async callback
```

- The callback runs after the outermost transaction commits; never when it rolls back, and not
  when the nested `transaction()` block it was registered in rolls back.
- Outside a transaction it runs at once.
- Callbacks run outside the finished transaction (their own queries autocommit), in order. An
  exception propagates from the end of the `async with` — the data is already committed — or,
  with `on_commit(callback, robust=True)`, is logged and the next callbacks still run.

## Signals

Signals let code react to events without the sender knowing about it — auditing, search
indexes, cache invalidation, plugins:

```python
from ferro.signals import post_save, receiver


@receiver(post_save, sender=Product)
async def reindex(sender, instance, created, **kwargs):
    await search.index(instance)
```

A receiver takes `sender` and `**kwargs` (signals may add arguments), may be `async`, and runs
on the event loop — keep sync receivers quick. Receivers run one after another in the order
they were connected; an exception stops the rest and propagates to the code that sent the
signal. When the sender and the receiver are both your code, call the function directly
instead: signals make the flow harder to follow.

| Signal (`ferro.signals`) | Sent | Arguments |
|---|---|---|
| `pre_save`, `post_save` | around `instance.save()` (also `create()`) | `sender` (model class), `instance`, `adding` / `created` |
| `pre_delete`, `post_delete` | around `instance.delete()` | `sender`, `instance` |
| `m2m_changed` | around many-to-many `add`/`remove`/`set`/`clear` | `sender` (link model), `instance`, `action`, `model`, `pk_set`, `reverse` |
| `request_started`, `request_finished` | around each routed request (not WebSockets) | `sender` (app), `request` |
| `got_request_exception` | a handler raised (not `HTTPException`) | `sender`, `request`, `exception` |
| `user_logged_in`, `user_logged_out` | after `login()` / `logout()` | `sender` (user class), `request`, `user` |
| `user_login_failed` | `authenticate()` refused | `sender` (`None`), `username`, `request`, `throttled` |
| `post_migrate` | after `ferro migrate` | `sender` (`None`), `applied` (migration names) |

Not sent (as in Django): `bulk_create()`, `bulk_update()`, `QuerySet.update()`,
`QuerySet.delete()`, and rows removed by a database cascade — they work in single statements.
Request signals are wired when the server starts: connect their receivers before (at import
time).

### Connecting

```python
from ferro.signals import Signal, post_delete

post_delete.connect(forget_image, sender=Photo)          # only Photo deletions
post_delete.connect(audit, dispatch_uid="audit")          # connect once, whatever the object
post_delete.disconnect(audit, dispatch_uid="audit")

order_paid = Signal("order_paid")                         # your own signal
await order_paid.send(Order, order=order)                 # [(receiver, result), ...]
await order_paid.send_robust(Order, order=order)          # exceptions logged and returned
```

Receivers are held by weak reference: a receiver nothing else refers to — a function defined
inside another function, a lambda — disappears. Connect those with `weak=False`. Connect
receivers at import time of a module your app imports (for example `signals.py` imported by
`app.py`), so they are in place before requests arrive.
