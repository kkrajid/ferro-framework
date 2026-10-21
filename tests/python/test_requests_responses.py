"""Feature 025: uploads, form/header/cookie parameters, dependencies, background tasks, files,
static files, redirects, url_for, OpenAPI and templates."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from ferro import (
    App,
    BackgroundTasks,
    ConfigurationError,
    Cookie,
    Depends,
    FileResponse,
    Form,
    Header,
    HTMLResponse,
    HTTPException,
    PlainTextResponse,
    RedirectResponse,
    UploadFile,
)
from ferro.testing import TestClient

# -- uploads and forms --------------------------------------------------------------------------


def upload_app(saved: Path) -> App:
    app = App()

    @app.post("/upload", max_body_size=4 * 1024 * 1024)
    async def upload(photo: UploadFile, caption: str = Form("none"), count: int = Form(1)):
        target = await photo.save(saved / "photo.bin")
        return {
            "filename": photo.filename,
            "raw": photo.raw_filename,
            "type": photo.content_type,
            "size": photo.size,
            "caption": caption,
            "count": count,
            "saved": target.stat().st_size,
        }

    @app.post("/many")
    async def many(files: list[UploadFile], note: UploadFile | None = None):
        return {"names": [f.filename for f in files], "note": note is not None}

    @app.post("/form")
    async def form(request):
        data = await request.form()
        return {
            "fields": {k: v for k, v in data.items() if isinstance(v, str)},
            "files": {k: [f.size for f in v] for k, v in data.files.items()},
        }

    @app.post("/small")
    async def small(file: UploadFile):
        return {"size": file.size}

    return app


def test_uploads_and_form_fields(tmp_path):
    with TestClient(upload_app(tmp_path)) as client:
        payload = bytes(range(256)) * 4096  # 1 MiB, more than the app-wide limit
        response = client.post(
            "/upload",
            data={"caption": "Hello, Kochi", "count": "3"},
            files={"photo": ("../../etc/passwd", payload, "image/png")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["filename"] == "passwd", "directories are stripped"
        assert body["raw"] == "../../etc/passwd"
        assert (body["type"], body["size"], body["saved"]) == (
            "image/png",
            len(payload),
            len(payload),
        )
        assert (body["caption"], body["count"]) == ("Hello, Kochi", 3)
        assert (tmp_path / "photo.bin").read_bytes() == payload

        many = client.post("/many", files={"files": [("a.txt", b"a"), ("b.txt", b"bb")]})
        assert many.json() == {"names": ["a.txt", "b.txt"], "note": False}

        form = client.post(
            "/form", data={"x": "1", "y": ["2", "3"]}, files={"f": [("1", b"12"), ("2", b"345")]}
        )
        assert form.json() == {"fields": {"x": "1", "y": "3"}, "files": {"f": [2, 3]}}

        missing = client.post("/upload", data={"caption": "no file"}, files={})
        assert missing.status_code == 422
        assert missing.json()["error"]["details"][0]["param"] == "photo"
        bad_count = client.post("/upload", data={"count": "x"}, files={"photo": ("p", b"1")})
        assert bad_count.status_code == 422
        assert client.post("/upload", json={"photo": 1}).status_code == 415
        too_big = client.post("/small", files={"file": ("big", payload)})
        assert too_big.status_code == 413, "the app-wide 1 MiB limit applies to other routes"
        broken = client.post(
            "/form",
            content=b"--b\r\nContent-Disposition: form-data\r\n\r\nx\r\n--b--",
            headers={"content-type": "multipart/form-data; boundary=b"},
        )
        assert broken.status_code == 400 and "field name" in broken.json()["error"]["message"]


def test_save_refuses_to_overwrite(tmp_path):
    async def run() -> None:
        upload = UploadFile("f", "a.txt", "text/plain", [], b"one")
        await upload.save(tmp_path / "a.txt")
        with pytest.raises(FileExistsError):
            await upload.save(tmp_path / "a.txt")
        await UploadFile("f", "a.txt", None, [], b"two").save(tmp_path / "a.txt", overwrite=True)

    asyncio.run(run())
    assert (tmp_path / "a.txt").read_bytes() == b"two"
    assert UploadFile("f", "C:\\x\\<evil>.exe", None, [], b"").filename == "_evil_.exe"


# -- headers, cookies, dependencies, background tasks --------------------------------------------


def test_headers_cookies_and_dependencies():
    app = App()
    log: list[str] = []

    async def database():
        log.append("open")
        yield {"conn": 1}
        log.append("close")

    async def current_user(token: str = Header(alias="x-token"), db=Depends(database)):
        if token != "secret":
            raise HTTPException(401, "bad token")
        return {"name": "ann", "db": db["conn"]}

    def pagination(page: int = 1, size: int = 10):
        return (page - 1) * size, size

    @app.get("/me")
    async def me(
        user=Depends(current_user),
        db=Depends(database),
        window=Depends(pagination),
        user_agent: str = Header("none"),
        theme: str = Cookie("light"),
    ):
        return {"user": user, "window": list(window), "agent": user_agent, "theme": theme}

    with TestClient(app) as client:
        ok = client.get(
            "/me?page=3", headers={"x-token": "secret", "user-agent": "t", "cookie": "theme=dark"}
        )
        assert ok.status_code == 200, ok.text
        assert ok.json() == {
            "user": {"name": "ann", "db": 1},
            "window": [20, 10],
            "agent": "t",
            "theme": "dark",
        }
        assert log == ["open", "close"], "a cached dependency runs once and cleans up"
        assert client.get("/me", headers={"x-token": "nope"}).status_code == 401
        missing = client.get("/me?page=x")
        assert missing.status_code == 422
        params = {d["param"] for d in missing.json()["error"]["details"]}
        assert params == {"x-token", "page"}


def test_background_tasks_run_after_the_response():
    app = App()
    done: list[str] = []

    async def slow_async(name: str):
        await asyncio.sleep(0.2)
        done.append(name)

    def sync_task(name: str):
        done.append(name)

    def failing():
        raise RuntimeError("logged, not raised")

    async def audit(tasks: BackgroundTasks):
        tasks.add_task(done.append, "from dependency")
        return True

    @app.post("/signup")
    async def signup(tasks: BackgroundTasks, audited=Depends(audit)):
        tasks.add_task(slow_async, "email")
        tasks.add_task(failing)
        tasks.add_task(sync_task, "metrics")
        return {"ok": audited}

    with TestClient(app) as client:
        started = time.monotonic()
        assert client.post("/signup").json() == {"ok": True}
        assert time.monotonic() - started < 0.2, "the response does not wait for the tasks"
        deadline = time.monotonic() + 5
        while len(done) < 3 and time.monotonic() < deadline:
            time.sleep(0.05)
    assert done == ["from dependency", "email", "metrics"]


def test_dependency_errors_at_registration():
    app = App()

    async def reads_body(file: UploadFile):
        return file

    with pytest.raises(ConfigurationError, match="reads the request body"):

        @app.post("/x")
        async def x(value=Depends(reads_body)): ...

    with pytest.raises(ConfigurationError, match="Depends"):

        @app.websocket("/ws")
        async def ws(socket, value=Depends(reads_body)): ...


# -- responses, files, static, url_for ----------------------------------------------------------


def files_app(folder: Path) -> App:
    app = App()
    (folder / "static" / "css").mkdir(parents=True)
    (folder / "static" / "css" / "site.css").write_text("body{color:red}")
    (folder / "static" / ".env").write_text("SECRET=1")
    (folder / "big.bin").write_bytes(b"x" * (2 * 1024 * 1024))
    (folder / "report.txt").write_text("hello report")
    app.static("/static", folder / "static")

    @app.get("/report")
    async def report(request):
        return FileResponse(
            folder / "report.txt", filename="Rapport été.txt", download=True, request=request
        )

    @app.get("/big")
    async def big(request):
        return FileResponse(folder / "big.bin", request=request)

    @app.get("/missing")
    async def missing():
        return FileResponse(folder / "nope.txt")

    @app.get("/page")
    async def page():
        return HTMLResponse("<h1>Hi</h1>")

    @app.get("/text")
    async def text():
        return PlainTextResponse("plain")

    @app.post("/go")
    async def go():
        return RedirectResponse(app.url_for("product", id=7, ref="mail"), status=303)

    @app.get("/products/{id}", name="product")
    async def product(id: int):
        return {"id": id}

    return app


def test_files_static_and_redirects(tmp_path):
    with TestClient(files_app(tmp_path)) as client:
        css = client.get("/static/css/site.css")
        assert css.status_code == 200 and css.text == "body{color:red}"
        assert css.headers["content-type"].startswith("text/css")
        assert css.headers["cache-control"] == "public, max-age=3600"
        etag = css.headers["etag"]
        assert (
            client.get("/static/css/site.css", headers={"if-none-match": etag}).status_code == 304
        )
        for bad in (
            "/static/.env",
            "/static/css",
            "/static/../report.txt",
            "/static/%2e%2e/report.txt",
            "/static/css/..%2f..%2freport.txt",
            "/static/nope.css",
        ):
            assert client.get(bad).status_code == 404, bad

        report = client.get("/report")
        assert report.text == "hello report"
        disposition = report.headers["content-disposition"]
        assert (
            disposition.startswith("attachment;")
            and "filename*=UTF-8''Rapport%20%C3%A9t%C3%A9.txt" in disposition
        )
        part = client.get("/report", headers={"range": "bytes=6-11"})
        assert (part.status_code, part.text) == (206, "report")
        assert part.headers["content-range"] == "bytes 6-11/12"
        assert client.get("/report", headers={"range": "bytes=50-60"}).status_code == 416
        whole = client.get("/report", headers={"range": "bytes=oops"})
        assert (whole.status_code, whole.text) == (200, "hello report"), "malformed: ignored"

        big = client.get("/big")
        assert big.status_code == 200 and len(big.content) == 2 * 1024 * 1024
        assert big.headers["content-length"] == str(2 * 1024 * 1024), "streamed with its length"
        tail = client.get("/big", headers={"range": "bytes=-10"})
        assert (tail.status_code, tail.content) == (206, b"x" * 10)
        assert client.get("/missing").status_code == 404

        page = client.get("/page")
        assert page.headers["content-type"] == "text/html; charset=utf-8"
        assert client.get("/text").headers["content-type"] == "text/plain; charset=utf-8"
        moved = client.post("/go")
        assert moved.status_code == 303 and moved.headers["location"] == "/products/7?ref=mail"


def test_url_for_and_redirect_checks(tmp_path):
    app = files_app(tmp_path)
    assert app.url_for("static", path="css/a b.css") == "/static/css/a%20b.css"
    assert app.url_for("product", id="a/b") == "/products/a%2Fb"
    with pytest.raises(ConfigurationError, match="missing path parameter"):
        app.url_for("product")
    with pytest.raises(ConfigurationError, match="no route named"):
        app.url_for("nope")
    with pytest.raises(ValueError):
        RedirectResponse("/x\r\nset-cookie: a=b")
    with pytest.raises(ValueError):
        RedirectResponse("/x", status=200)


# -- OpenAPI ------------------------------------------------------------------------------------


pydantic = pytest.importorskip("pydantic")


class ItemIn(pydantic.BaseModel):
    name: str
    price: float


class ItemOut(pydantic.BaseModel):
    id: int
    name: str


def test_openapi_document_and_docs_page():
    app = App()

    @app.post("/items", tags=["items"])
    async def create_item(data: ItemIn, dry_run: bool = False) -> ItemOut:
        """Creates an item.

        Longer description."""
        return ItemOut(id=1, name=data.name)

    @app.get("/items/{id}")
    async def read_item(id: int, token: str = Header(alias="x-token")) -> ItemOut:
        return ItemOut(id=id, name="x")

    @app.post("/avatar")
    async def avatar(photo: UploadFile, caption: str = Form()):
        return {}

    @app.get("/hidden", include_in_schema=False)
    async def hidden():
        return {}

    app.add_openapi(title="Shop", version="1.2.3")
    with TestClient(app) as client:
        document = client.get("/openapi.json").json()
        docs = client.get("/docs")
    assert document["openapi"] == "3.1.0" and document["info"] == {
        "title": "Shop",
        "version": "1.2.3",
    }
    assert set(document["paths"]) == {"/items", "/items/{id}", "/avatar"}
    create = document["paths"]["/items"]["post"]
    assert create["summary"] == "Creates an item." and create["tags"] == ["items"]
    assert create["parameters"] == [
        {
            "name": "dry_run",
            "in": "query",
            "required": False,
            "schema": {"type": "boolean", "default": False},
        }
    ]
    assert create["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ItemIn"
    }
    assert create["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ItemOut"
    }
    assert "422" in create["responses"]
    read = document["paths"]["/items/{id}"]["get"]
    assert {(p["name"], p["in"], p["schema"]["type"]) for p in read["parameters"]} == {
        ("id", "path", "integer"),
        ("x-token", "header", "string"),
    }
    upload = document["paths"]["/avatar"]["post"]["requestBody"]["content"]["multipart/form-data"]
    assert upload["schema"]["properties"]["photo"] == {"type": "string", "format": "binary"}
    assert set(upload["schema"]["required"]) == {"photo", "caption"}
    assert {"ItemIn", "ItemOut", "Error"} <= set(document["components"]["schemas"])
    assert docs.status_code == 200 and "swagger-ui" in docs.text
    assert "cdn.jsdelivr.net" in docs.headers["content-security-policy"]


# -- templates ----------------------------------------------------------------------------------


def test_templates_escape_and_url_for(tmp_path):
    pytest.importorskip("jinja2")
    from ferro.templating import Templates

    (tmp_path / "page.html").write_text(
        "<p>{{ name }}</p><a href=\"{{ url_for('product', id=pk) }}\">x</a>"
    )
    app = App()

    @app.get("/products/{id}", name="product")
    async def product(id: int):
        return {}

    templates = Templates(tmp_path, app=app)

    @app.get("/page")
    async def page():
        return templates.render("page.html", {"name": "<script>alert(1)</script>", "pk": 5})

    with TestClient(app) as client:
        response = client.get("/page")
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert "&lt;script&gt;" in response.text and "<script>" not in response.text
    assert 'href="/products/5"' in response.text
