"""Feature 024: signals, on_commit and application lifecycle hooks."""

from __future__ import annotations

import gc
import logging

import pytest

from ferro import App, HTTPException, db, models
from ferro.db import on_commit, transaction
from ferro.signals import (
    Signal,
    got_request_exception,
    m2m_changed,
    post_delete,
    post_save,
    pre_delete,
    pre_save,
    receiver,
    request_finished,
    request_started,
)
from ferro.testing import TestClient

from .conftest import use_models


class Tag(models.Model):
    name = models.String(max_length=20)

    class Meta:
        table = "t24_tag"


class Post(models.Model):
    title = models.String(max_length=40)
    tags = models.ManyToMany(Tag, related_name="posts")

    class Meta:
        table = "t24_post"


@pytest.fixture
def connected():
    """Connects receivers for one test and disconnects them afterwards."""
    made: list[tuple[Signal, object, object]] = []

    def connect(signal: Signal, function, sender=None):
        signal.connect(function, sender=sender, weak=False)
        made.append((signal, function, sender))
        return function

    yield connect
    for signal, function, sender in made:
        signal.disconnect(function, sender=sender)


# -- Signal ------------------------------------------------------------------------------------


async def test_connect_send_and_disconnect():
    ping = Signal("ping")
    seen = []

    def sync_receiver(sender, value, **kwargs):
        seen.append(("sync", sender, value, kwargs["signal"] is ping))
        return "s"

    async def async_receiver(sender, **kwargs):
        seen.append(("async", sender))
        return "a"

    ping.connect(sync_receiver)
    ping.connect(sync_receiver)  # once only
    ping.connect(async_receiver, sender=str)
    results = await ping.send(str, value=1)
    assert [r for _, r in results] == ["s", "a"]
    assert seen == [("sync", str, 1, True), ("async", str)]
    seen.clear()
    await ping.send(int, value=2)  # async_receiver listens to str only
    assert seen == [("sync", int, 2, True)]
    assert ping.disconnect(sync_receiver) is True
    assert ping.disconnect(sync_receiver) is False
    assert ping.has_listeners(str) and not ping.has_listeners(int)

    with pytest.raises(TypeError, match=r"\*\*kwargs"):
        ping.connect(lambda sender: None)
    with pytest.raises(TypeError, match="callable"):
        ping.connect("nope")  # type: ignore[arg-type]


async def test_weak_references_and_dispatch_uid():
    ping = Signal("ping")
    calls = []

    def make():
        def local(sender, **kwargs):
            calls.append("local")

        return local

    ping.connect(make())  # weak: nothing else keeps it
    gc.collect()
    await ping.send(None)
    assert calls == []

    class Handler:
        def on(self, sender, **kwargs):
            calls.append(id(self))

    first, second = Handler(), Handler()
    ping.connect(first.on, dispatch_uid="handler")
    ping.connect(second.on, dispatch_uid="handler")  # same uid: kept once
    await ping.send(None)
    assert calls == [id(first)]
    del first
    gc.collect()
    await ping.send(None)
    assert calls == [calls[0]], "the bound method died with its object"
    assert ping.disconnect(dispatch_uid="handler") is False


async def test_send_robust_and_receiver_decorator(caplog):
    one, two = Signal("one"), Signal("two")
    calls = []

    @receiver([one, two], weak=False)
    def listener(sender, **kwargs):
        calls.append(kwargs["signal"].name)
        raise ValueError("boom")

    def after(sender, **kwargs):
        calls.append("after")

    one.connect(after, weak=False)
    with caplog.at_level(logging.ERROR, logger="ferro.signals"):
        results = await one.send_robust(None)
    assert isinstance(results[0][1], ValueError) and results[1][1] is None
    assert calls == ["one", "after"] and "receiver" in caplog.text
    with pytest.raises(ValueError, match="boom"):
        await two.send(None)


# -- model signals -----------------------------------------------------------------------------


async def test_model_signals(database, connected):
    await use_models(Tag, Post)
    events = []

    def record(name):
        def handler(sender, instance, **kwargs):
            extra = {k: v for k, v in kwargs.items() if k in ("adding", "created", "action")}
            events.append((name, sender.__name__, getattr(instance, "title", None), extra))

        return handler

    connected(pre_save, record("pre_save"), sender=Post)
    connected(post_save, record("post_save"), sender=Post)
    connected(pre_delete, record("pre_delete"))
    connected(post_delete, record("post_delete"))

    post = await Post.objects.create(title="Hello")
    post.title = "Hi"
    await post.save()
    await Tag.objects.create(name="x")  # sender filter: no save events for Tag
    pk = post.pk
    await post.delete()
    assert events == [
        ("pre_save", "Post", "Hello", {"adding": True}),
        ("post_save", "Post", "Hello", {"created": True}),
        ("pre_save", "Post", "Hi", {"adding": False}),
        ("post_save", "Post", "Hi", {"created": False}),
        ("pre_delete", "Post", "Hi", {}),
        ("post_delete", "Post", "Hi", {}),
    ]
    assert pk is not None and post.pk is None


async def test_m2m_changed(database, connected):
    await use_models(Tag, Post)
    changes = []

    def on_change(sender, instance, action, model, pk_set, reverse, **kwargs):
        changes.append((action, model.__name__, sorted(pk_set) if pk_set else pk_set, reverse))

    connected(m2m_changed, on_change)
    post = await Post.objects.create(title="p")
    a, b = await Tag.objects.create(name="a"), await Tag.objects.create(name="b")
    await post.tags.add(a, b)
    await post.tags.remove(a)
    await post.tags.set([a])
    await a.posts.clear()
    assert changes == [
        ("pre_add", "Tag", [a.pk, b.pk], False),
        ("post_add", "Tag", [a.pk, b.pk], False),
        ("pre_remove", "Tag", [a.pk], False),
        ("post_remove", "Tag", [a.pk], False),
        ("pre_remove", "Tag", [b.pk], False),
        ("post_remove", "Tag", [b.pk], False),
        ("pre_add", "Tag", [a.pk], False),
        ("post_add", "Tag", [a.pk], False),
        ("pre_clear", "Post", None, True),
        ("post_clear", "Post", None, True),
    ]


# -- on_commit ---------------------------------------------------------------------------------


async def test_on_commit(database, caplog):
    await use_models(Tag, Post)
    ran = []

    async def count_posts():
        ran.append(("posts", await Post.objects.count()))

    async with transaction():
        await Post.objects.create(title="kept")
        await on_commit(count_posts)
        await on_commit(lambda: ran.append("sync"))
        assert ran == []
    assert ran == [("posts", 1), "sync"]

    ran.clear()
    with pytest.raises(RuntimeError):
        async with transaction():
            await on_commit(lambda: ran.append("never"))
            raise RuntimeError("rolled back")
    assert ran == []

    async with transaction():
        await on_commit(lambda: ran.append("outer"))
        with pytest.raises(RuntimeError):
            async with transaction():
                await on_commit(lambda: ran.append("inner rolled back"))
                raise RuntimeError
        async with transaction():
            await on_commit(lambda: ran.append("inner kept"))
    assert ran == ["outer", "inner kept"]

    ran.clear()
    await on_commit(lambda: ran.append("now"))  # no transaction: at once
    assert ran == ["now"]

    def broken():
        raise ValueError("callback failed")

    with caplog.at_level(logging.ERROR, logger="ferro.db"):
        async with transaction():
            await on_commit(broken, robust=True)
            await on_commit(lambda: ran.append("still runs"))
    assert ran == ["now", "still runs"] and "callback failed" in caplog.text
    with pytest.raises(ValueError, match="callback failed"):
        async with transaction():
            await Post.objects.create(title="committed anyway")
            await on_commit(broken)
    assert await Post.objects.filter(title="committed anyway").exists()

    @transaction()
    async def decorated():
        await on_commit(lambda: ran.append("decorated"))

    await decorated()
    assert ran[-1] == "decorated"
    assert db.on_commit is on_commit


# -- lifecycle hooks and request signals ----------------------------------------------------------


def test_startup_and_shutdown_hooks():
    app = App()
    log: list[str] = []

    @app.on_startup
    async def open_client():
        app.state = {"client": "open"}  # type: ignore[attr-defined]
        log.append("startup")

    @app.on_startup
    def sync_startup():
        log.append("sync startup")

    @app.on_shutdown
    async def close_client():
        log.append("shutdown")

    @app.on_shutdown
    def failing_shutdown():
        log.append("failing shutdown")
        raise RuntimeError("ignored")

    @app.get("/state")
    async def state():
        return app.state  # type: ignore[attr-defined]

    with TestClient(app) as client:
        assert log == ["startup", "sync startup"]
        assert client.get("/state").json() == {"client": "open"}
    assert log == ["startup", "sync startup", "failing shutdown", "shutdown"]

    broken = App()

    @broken.on_startup
    def fail():
        raise RuntimeError("cannot start")

    with pytest.raises(RuntimeError, match="cannot start"), TestClient(broken):
        pass


def test_request_signals(connected):
    app = App()

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/missing")
    async def missing():
        raise HTTPException(404)

    @app.get("/boom")
    async def boom():
        raise ValueError("handler failed")

    seen = []
    connected(request_started, lambda sender, request, **kw: seen.append(("start", request.path)))
    connected(request_finished, lambda sender, request, **kw: seen.append(("end", request.path)))
    connected(
        got_request_exception,
        lambda sender, request, exception, **kw: seen.append(("error", str(exception))),
    )
    with TestClient(app) as client:
        assert client.get("/ok").status_code == 200
        assert client.get("/missing").status_code == 404
        assert client.get("/boom").status_code == 500
    assert seen == [
        ("start", "/ok"),
        ("end", "/ok"),
        ("start", "/missing"),
        ("end", "/missing"),
        ("start", "/boom"),
        ("error", "handler failed"),
        ("end", "/boom"),
    ]
