"""Feature 009 — `ferro.testing.TestClient`."""

from __future__ import annotations

from ferro import App, Response, models
from ferro.testing import TestClient


class Note(models.Model):
    text = models.String(max_length=50)

    class Meta:
        table = "t9_note"


def notes_app() -> App:
    app = App()

    @app.get("/")
    async def home(request):
        return {"hello": "world", "cookie": request.cookies.get("visited")}

    @app.post("/notes")
    async def create(request):
        data = await request.json()
        note = await Note.objects.create(text=data["text"])
        response = Response(note.to_dict(), status=201)
        response.set_cookie("visited", "yes")
        return response

    @app.post("/form")
    async def form(request):
        return dict((await request.form()).items())

    @app.get("/notes")
    async def listing():
        return [n.text for n in await Note.objects.order_by("pk")]

    return app


def test_requests_json_forms_cookies_and_database():
    with TestClient(notes_app(), database_url="sqlite:///:memory:", create_tables=[Note]) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert home.json() == {"hello": "world", "cookie": None}
        assert home.headers["content-type"] == "application/json"
        created = client.post("/notes", json={"text": "hi"})
        assert created.status_code == 201 and created.json()["text"] == "hi"
        assert client.get("/").json()["cookie"] == "yes", "cookies are kept"
        assert client.get("/notes").json() == ["hi"]
        assert client.post("/form", data={"a": "1", "b": "two words"}).json() == {
            "a": "1",
            "b": "two words",
        }
        assert client.get("/missing").status_code == 404
        assert "Not Found" in client.get("/missing").text
