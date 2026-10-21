"""Uploaded files and multipart forms (Feature 025).

```python
from ferro import UploadFile


@app.post("/avatar", max_body_size=10 * 1024 * 1024)
async def avatar(photo: UploadFile, caption: str = Form("")):
    if photo.content_type not in ("image/png", "image/jpeg"):
        raise HTTPException(415, "PNG or JPEG only")
    await photo.save(MEDIA / f"{uuid4().hex}.img")
```

Bodies are read into memory up to the route's ``max_body_size`` (1 MiB by default), so a file
is already complete when the handler runs. Never trust ``filename`` or ``content_type``: they
come from the client. ``filename`` keeps only the last path component.
"""

from __future__ import annotations

import asyncio
import io
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

__all__ = ["FormData", "UploadFile"]

_UNSAFE = {chr(c) for c in range(32)} | {"\x7f", "/", "\\", ":", "*", "?", '"', "<", ">", "|"}


def _safe_name(raw: str) -> str:
    """The last path component of a client-supplied name, without control characters and
    characters invalid in file names; "" when nothing usable remains."""
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join("_" if ch in _UNSAFE else ch for ch in name).strip(" .")
    return name[:255]


class UploadFile:
    """A file sent in a ``multipart/form-data`` body."""

    __slots__ = ("_data", "content_type", "field", "filename", "headers", "raw_filename")

    def __init__(
        self,
        field: str,
        filename: str,
        content_type: str | None,
        headers: list[tuple[str, str]],
        data: bytes,
    ) -> None:
        self.field = field
        #: As the client sent it (may contain a path); for display, never for storage paths.
        self.raw_filename = filename
        #: Last path component, with unsafe characters replaced (may still be ``""``).
        self.filename = _safe_name(filename)
        #: As the client declared it (``None`` when absent): check the content yourself.
        self.content_type = content_type
        self.headers = headers
        self._data = data

    @property
    def size(self) -> int:
        return len(self._data)

    async def read(self) -> bytes:
        """The file's content."""
        return self._data

    @property
    def file(self) -> io.BytesIO:
        """A new binary file object over the content (for libraries that want one)."""
        return io.BytesIO(self._data)

    async def save(self, path: str | os.PathLike[str], *, overwrite: bool = False) -> Path:
        """Writes the content to ``path`` (in a worker thread) and returns it. An existing file
        is an error unless ``overwrite=True``. Choose ``path`` yourself — not from
        ``filename``, which the client controls."""
        target = Path(path)
        mode = "wb" if overwrite else "xb"

        def write() -> None:
            with open(target, mode) as handle:
                handle.write(self._data)

        await asyncio.get_running_loop().run_in_executor(None, write)
        return target

    def __repr__(self) -> str:
        return (
            f"UploadFile(field={self.field!r}, filename={self.filename!r}, "
            f"content_type={self.content_type!r}, size={self.size})"
        )


class FormData:
    """The fields of a form body: text values (``str``) and, for ``multipart/form-data``,
    :class:`UploadFile` objects. ``get`` returns the last value of a field, ``getlist`` every
    value."""

    __slots__ = ("_items",)

    def __init__(self, items: list[tuple[str, Any]]) -> None:
        self._items = items

    def get(self, key: str, default: Any = None) -> Any:
        for name, value in reversed(self._items):
            if name == key:
                return value
        return default

    def getlist(self, key: str) -> list[Any]:
        return [value for name, value in self._items if name == key]

    def items(self) -> list[tuple[str, Any]]:
        return list(self._items)

    def keys(self) -> list[str]:
        return list(dict.fromkeys(name for name, _ in self._items))

    @property
    def files(self) -> dict[str, list[UploadFile]]:
        """Uploaded files by field name."""
        found: dict[str, list[UploadFile]] = {}
        for name, value in self._items:
            if isinstance(value, UploadFile):
                found.setdefault(name, []).append(value)
        return found

    def __getitem__(self, key: str) -> Any:
        missing = object()
        value = self.get(key, missing)
        if value is missing:
            raise KeyError(key)
        return value

    def __contains__(self, key: object) -> bool:
        return any(name == key for name, _ in self._items)

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self.keys())

    def __repr__(self) -> str:
        return f"FormData({self._items!r})"
