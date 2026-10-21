"""Files on disk as responses, and static files (Feature 025).

```python
from ferro import FileResponse

@app.get("/reports/{id}")
async def report(request, id: int):
    return FileResponse(REPORTS / f"{id}.pdf", filename="report.pdf", request=request)

app.static("/static", "static")          # serves ./static/... at /static/...
```
"""

from __future__ import annotations

import asyncio
import email.utils
import hashlib
import mimetypes
import os
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ferro.exceptions import HTTPException
from ferro.response import HeadersInput, Response, StreamingResponse

__all__ = ["FileResponse", "StaticFiles"]

CHUNK = 256 * 1024
# Read into memory at once below this size (one response body instead of a stream).
SMALL = 512 * 1024


def _disposition(filename: str, download: bool) -> str:
    kind = "attachment" if download else "inline"
    ascii_name = filename.encode("ascii", "ignore").decode() or "download"
    ascii_name = ascii_name.replace("\\", "_").replace('"', "_")
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _etag(info: os.stat_result) -> str:
    raw = f"{info.st_mtime_ns}-{info.st_size}".encode()
    return '"' + hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:20] + '"'


async def _chunks(path: Path, start: int, length: int) -> AsyncIterator[bytes]:
    loop = asyncio.get_running_loop()
    handle = await loop.run_in_executor(None, open, path, "rb")
    try:
        await loop.run_in_executor(None, handle.seek, start)
        remaining = length
        while remaining > 0:
            chunk = await loop.run_in_executor(None, handle.read, min(CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        await loop.run_in_executor(None, handle.close)


_UNSATISFIABLE = (-1, -1)


def _byte_range(header: str, size: int) -> tuple[int, int] | None:
    """``(start, end)`` (inclusive) of a single ``bytes=`` range; ``None`` for a header to
    ignore (malformed, several ranges: the whole file is sent); ``_UNSATISFIABLE`` when the
    range lies outside the file (RFC 9110 section 14.1.2)."""
    if not header.startswith("bytes=") or "," in header:
        return None
    first, _, last = header[6:].strip().partition("-")
    try:
        if not first:
            suffix = int(last)
            if suffix <= 0:
                return _UNSATISFIABLE
            return max(0, size - suffix), size - 1
        start = int(first)
        end = int(last) if last else size - 1
    except ValueError:
        return None
    if start < 0 or start > end:
        return None
    if start >= size:
        return _UNSATISFIABLE
    return start, min(end, size - 1)


class FileResponse(Response):
    """A file from disk: its media type (guessed from the name unless given), size,
    ``Last-Modified`` and ``ETag``. With ``request``, conditional requests get ``304`` and a
    ``Range`` header gets ``206`` with that part. ``filename`` sets ``Content-Disposition``
    (``download=True`` asks the browser to save it). Large files are streamed from disk."""

    __slots__ = ("_stream",)

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        filename: str | None = None,
        download: bool = False,
        media_type: str | None = None,
        status: int = 200,
        headers: HeadersInput = None,
        request: Any = None,
        cache_max_age: int | None = None,
    ) -> None:
        target = Path(path)
        try:
            info = target.stat()
        except OSError:
            raise HTTPException(404, "File not found") from None
        if not stat.S_ISREG(info.st_mode):
            raise HTTPException(404, "File not found")
        media = media_type or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if media.startswith("text/") or media in ("application/javascript", "image/svg+xml"):
            media += "; charset=utf-8" if "charset" not in media else ""
        super().__init__(None, status, headers, media)
        etag = _etag(info)
        modified = email.utils.formatdate(info.st_mtime, usegmt=True)
        self.headers += [("etag", etag), ("last-modified", modified), ("accept-ranges", "bytes")]
        if cache_max_age is not None:
            self.headers.append(("cache-control", f"public, max-age={int(cache_max_age)}"))
        if filename is not None or download:
            self.headers.append(
                ("content-disposition", _disposition(filename or target.name, download))
            )
        self._stream = None
        size, start, length = info.st_size, 0, info.st_size
        if request is not None and status == 200:
            if self._not_modified(request, etag, info):
                self.status, self.media_type = 304, None
                return
            wanted = request.headers.get("range")
            condition = request.headers.get("if-range")
            if wanted and (condition is None or condition in (etag, modified)):
                part = _byte_range(wanted, size)
                if part == _UNSATISFIABLE:
                    self.status = 416
                    self.headers.append(("content-range", f"bytes */{size}"))
                    self.content = b""
                    return
                if part is not None:
                    start, end = part
                    length = end - start + 1
                    self.status = 206
                    self.headers.append(("content-range", f"bytes {start}-{end}/{size}"))
        if length <= SMALL:
            with open(target, "rb") as handle:
                handle.seek(start)
                self.content = handle.read(length)
            return
        self.headers.append(("content-length", str(length)))
        self._stream = StreamingResponse(_chunks(target, start, length))._iterator

    @staticmethod
    def _not_modified(request: Any, etag: str, info: os.stat_result) -> bool:
        if request.method not in ("GET", "HEAD"):
            return False
        match = request.headers.get("if-none-match")
        if match is not None:
            return match.strip() == "*" or etag in [tag.strip() for tag in match.split(",")]
        since = request.headers.get("if-modified-since")
        if since:
            try:
                moment = email.utils.parsedate_to_datetime(since).timestamp()
            except (TypeError, ValueError):
                return False
            return int(info.st_mtime) <= moment
        return False

    @property
    def _iterator(self) -> Any:
        return self._stream


class StaticFiles:
    """Serves files below ``directory`` (``app.static(prefix, directory)``). Paths are
    resolved and must stay inside the directory; hidden files (names starting with ``.``) and
    directories are not served; ``index.html`` answers a directory when ``html=True``."""

    def __init__(
        self,
        directory: str | os.PathLike[str],
        *,
        html: bool = False,
        cache_max_age: int | None = 3600,
    ) -> None:
        self.directory = Path(directory).resolve()
        if not self.directory.is_dir():
            from ferro.exceptions import ConfigurationError

            raise ConfigurationError(f"static directory {self.directory} does not exist")
        self.html = html
        self.cache_max_age = cache_max_age

    def resolve(self, relative: str) -> Path | None:
        parts = relative.replace("\\", "/").split("/")
        if any(p in ("", ".", "..") or p.startswith(".") or "\x00" in p for p in parts if p != ""):
            return None
        if any(":" in p for p in parts):  # drive letters and NTFS streams
            return None
        candidate = (self.directory / "/".join(p for p in parts if p)).resolve()
        if candidate != self.directory and self.directory not in candidate.parents:
            return None
        if candidate.is_dir():
            if not self.html:
                return None
            candidate = candidate / "index.html"
        return candidate if candidate.is_file() else None

    async def __call__(self, request: Any, path: str) -> FileResponse:
        target = self.resolve(path)
        if target is None:
            raise HTTPException(404, "File not found")
        return FileResponse(target, request=request, cache_max_age=self.cache_max_age)
