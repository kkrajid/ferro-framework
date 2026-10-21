"""Feature 026: uploads over 1 MiB are kept in a temporary file and read in pieces."""

from __future__ import annotations

import hashlib
import io
import os

from ferro import App, Form, UploadFile, _ferro
from ferro.testing import TestClient

SIZE = 5 * 1024 * 1024 + 123


def test_large_upload_is_a_view_read_in_pieces(tmp_path):
    payload = os.urandom(SIZE)
    app = App()

    @app.post("/upload", max_body_size=8 * 1024 * 1024)
    async def upload(photo: UploadFile, note: str = Form()):
        digest = hashlib.sha256()
        pieces = 0
        async for chunk in photo.chunks(1024 * 1024):
            digest.update(chunk)
            pieces += 1
        handle = photo.file
        handle.seek(-10, io.SEEK_END)
        tail = handle.read()
        handle.seek(100)
        middle = handle.read(5)
        saved = await photo.save(tmp_path / "photo.bin")
        whole = await photo.read()
        return {
            "view": isinstance(photo._data, _ferro.BodySlice),
            "size": photo.size,
            "sha": digest.hexdigest(),
            "pieces": pieces,
            "tail": tail.hex(),
            "middle": middle.hex(),
            "saved": saved.stat().st_size,
            "whole": hashlib.sha256(whole).hexdigest(),
            "note": note,
        }

    with TestClient(app) as client:
        answer = client.post(
            "/upload",
            files={"photo": ("photo.bin", payload, "application/octet-stream")},
            data={"note": "big"},
        ).json()
    expected = hashlib.sha256(payload).hexdigest()
    assert answer == {
        "view": True,
        "size": SIZE,
        "sha": expected,
        "pieces": 6,
        "tail": payload[-10:].hex(),
        "middle": payload[100:105].hex(),
        "saved": SIZE,
        "whole": expected,
        "note": "big",
    }
    assert hashlib.sha256((tmp_path / "photo.bin").read_bytes()).hexdigest() == expected


def test_small_upload_reads_the_same_way():
    app = App()

    @app.post("/small")
    async def small(doc: UploadFile):
        pieces = [chunk async for chunk in doc.chunks(4)]
        return {"pieces": [p.decode() for p in pieces], "file": doc.file.read().decode()}

    with TestClient(app) as client:
        answer = client.post("/small", files={"doc": ("a.txt", b"hello world", "text/plain")})
    assert answer.json() == {"pieces": ["hell", "o wo", "rld"], "file": "hello world"}
