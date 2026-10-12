"""Feature 008 — `request.form()` for urlencoded bodies."""

from ferro import App


def form_app() -> App:
    app = App()

    @app.post("/form")
    async def form(request):
        data = await request.form()
        return {
            "name": data.get("name"),
            "tags": data.getlist("tag"),
            "missing": data.get("missing", "default"),
            "keys": sorted(data.keys()),
            "contains": "name" in data,
            "item": data["name"] if "name" in data else None,  # noqa: SIM401 - tests __getitem__
        }

    return app


def test_urlencoded_forms_are_parsed(serve):
    client = serve(form_app())
    response = client.post(
        "/form",
        content="name=caf%C3%A9+au+lait&tag=a&tag=b&empty=",
        headers={"content-type": "application/x-www-form-urlencoded; charset=utf-8"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "name": "café au lait",
        "tags": ["a", "b"],
        "missing": "default",
        "keys": ["empty", "name", "tag"],
        "contains": True,
        "item": "café au lait",
    }


def test_empty_and_wrong_bodies(serve):
    client = serve(form_app())
    empty = client.post("/form", headers={"content-type": "application/x-www-form-urlencoded"})
    assert empty.json()["keys"] == []
    wrong = client.post("/form", json={"name": "x"})
    assert wrong.status_code == 400
    assert "form" in wrong.json()["error"]["message"]
    invalid = client.post(
        "/form",
        content=b"name=%FF%FE",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert invalid.status_code == 400
