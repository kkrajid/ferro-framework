"""Parameter markers for handlers (Feature 025): where a value comes from when its name alone
does not say it.

```python
from ferro import Cookie, Depends, Form, Header


async def current_user(request, token: str = Header(alias="x-api-token")):
    user = await User.objects.filter(api_token=token).first()
    if user is None:
        raise HTTPException(401, "Invalid token")
    return user


@app.post("/notes")
async def create(title: str = Form(), user: User = Depends(current_user)): ...
```

Parameters without a marker keep their meaning: ``request``, path parameters by name, query
parameters otherwise, a Pydantic model for the body, ``UploadFile`` for files.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

__all__ = ["Cookie", "Depends", "Form", "Header"]

_REQUIRED: Any = ...


class _Marker:
    __slots__ = ("alias", "default")
    source = ""

    def __init__(self, default: Any = _REQUIRED, *, alias: str | None = None) -> None:
        self.default = default
        self.alias = alias

    @property
    def required(self) -> bool:
        return self.default is _REQUIRED

    def __repr__(self) -> str:
        shown = "" if self.required else repr(self.default)
        return f"{type(self).__name__}({shown})"


class Form(_Marker):
    """A field of a form body (``application/x-www-form-urlencoded`` or
    ``multipart/form-data``); ``Form()`` is required, ``Form("x")`` defaults to ``"x"``."""

    __slots__ = ()
    source = "form"


class Header(_Marker):
    """A request header; the parameter name with ``_`` read as ``-`` (``user_agent`` →
    ``user-agent``) unless ``alias`` names it."""

    __slots__ = ()
    source = "header"


class Cookie(_Marker):
    """A request cookie of the parameter's name (or ``alias``)."""

    __slots__ = ()
    source = "cookie"


class Depends:
    """A value computed by ``dependency`` for each request: a function (sync or ``async``)
    whose own parameters are filled like a handler's (request, path, query, headers, cookies,
    other dependencies, ``BackgroundTasks``). A generator dependency (``yield`` once) runs its
    code after ``yield`` when the handler has returned — for cleanup. Within one request a
    dependency used twice runs once unless ``use_cache=False``."""

    __slots__ = ("dependency", "use_cache")

    def __init__(self, dependency: Callable[..., Any], *, use_cache: bool = True) -> None:
        if not callable(dependency):
            raise TypeError(f"Depends() takes a function, got {dependency!r}")
        self.dependency = dependency
        self.use_cache = use_cache

    def __repr__(self) -> str:
        return f"Depends({getattr(self.dependency, '__qualname__', self.dependency)!r})"
