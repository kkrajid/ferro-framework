# Tutorial: a notes API with users and an admin

You will build a small JSON API where people register, log in and keep private notes with tags,
plus an admin back office — then test it and run it like production. It takes about 20 minutes.
Every piece of code below is tested.

Prerequisite: Ferro installed ([Installation](01-installation.md)).

## 1. Create the project

```bash
ferro new notes
```

```bash
cd notes
```

`ferro new` wrote `app.py`, `models.py`, `admin.py`, `tests/`, `.env` (with
`FERRO_DATABASE_URL=sqlite:///db.sqlite3`) and more. Every `ferro` command reads `.env`.

## 2. Models

Replace `models.py`:

```python
"""Database models.

After changing them run `ferro makemigrations` and `ferro migrate`.
"""

from datetime import UTC, datetime

from ferro import models
from ferro.auth import User


def now() -> datetime:
    return datetime.now(UTC)


class Tag(models.Model):
    name = models.String(max_length=30, unique=True)

    def __str__(self) -> str:
        return self.name


class Note(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notes")
    title = models.String(max_length=200)
    body = models.Text(default="")
    pinned = models.Boolean(default=False)
    created = models.DateTime(default=now)
    tags = models.ManyToMany(Tag, related_name="notes")

    def __str__(self) -> str:
        return self.title
```

- Every model gets an automatic `id` primary key.
- `ForeignKey(User, on_delete=CASCADE)` stores `owner_id`; deleting a user deletes their notes.
- `ManyToMany(Tag)` creates a link table `note_tags`.
- `default=now` is called for each new row (a callable), not once.

## 3. The admin

Replace `admin.py`:

```python
"""Models shown in the admin at /admin."""

from ferro.admin import ModelAdmin, admin
from ferro.auth import User
from models import Note, Tag

admin.register(
    Note,
    ModelAdmin(
        list_display=["title", "owner", "pinned", "created"],
        search_fields=["title", "owner__username"],
        list_filter=["pinned"],
        ordering=["-created"],
        readonly_fields=["created"],
    ),
)
admin.register(Tag, ModelAdmin(list_display=["name"], search_fields=["name"]))
admin.register(User, ModelAdmin(list_display=["username", "is_staff", "is_active"]))
```

## 4. Create the database

```bash
ferro makemigrations
```

This writes `migrations/0001_initial.py` — tables for your models plus Ferro's users, groups,
permissions and sessions. Read it: it is plain Python. Then apply it and create an admin user:

```bash
ferro migrate
```

```bash
ferro createsuperuser --username admin
```

(It asks for a password. In scripts: `FERRO_SUPERUSER_PASSWORD=... ferro createsuperuser
--username admin --no-input`.)

## 5. The API

Replace `app.py`:

```python
"""notes: a Ferro application."""

import admin  # noqa: F401 - registers the models with the admin site
from ferro import App, HTTPException, Response, ValidationError, auth
from ferro.auth import User, authenticate, login_required
from ferro.models import IntegrityError
from models import Note, Tag

app = App(rate_limit="120/minute")
app.add_admin()  # the admin at /admin; also turns on request.user and request.session


async def note_json(note: Note) -> dict:
    tags = await note.tags.all()  # already loaded by prefetch_related: no query
    return {**note.to_dict(), "tags": [tag.name for tag in tags]}


async def tags_named(names: list[str]) -> list[Tag]:
    tags = []
    for name in names:
        tag = await Tag.objects.filter(name=name).first()
        tags.append(tag or await Tag.objects.create(name=name))
    return tags


# -- accounts -------------------------------------------------------------------------------


@app.post("/register", rate_limit="5/minute")
async def register(request):
    data = await request.json()
    try:
        user = await User.objects.create_user(data["username"], password=data["password"])
    except IntegrityError:
        raise HTTPException(409, "That username is taken") from None
    await auth.login(request, user)
    return {"username": user.username}


@app.post("/login", rate_limit="5/minute")
async def sign_in(request):
    data = await request.json()
    user = await authenticate(data.get("username", ""), data.get("password", ""))
    if user is None:
        raise HTTPException(401, "Wrong username or password")
    await auth.login(request, user)
    return {"username": user.username}


@app.post("/logout")
async def sign_out(request):
    await auth.logout(request)


# -- notes ----------------------------------------------------------------------------------


@app.get("/notes")
@login_required
async def list_notes(request, q: str = "", pinned: bool | None = None, page: int = 1):
    notes = Note.objects.filter(owner=request.user).order_by("-pinned", "-created")
    if q:
        notes = notes.filter(title__icontains=q)
    if pinned is not None:
        notes = notes.filter(pinned=pinned)
    start = (max(page, 1) - 1) * 20
    found = await notes.prefetch_related("tags")[start : start + 20]
    return [await note_json(note) for note in found]


@app.post("/notes")
@login_required
async def create_note(request):
    data = await request.json()
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValidationError([{"param": "title", "location": "body", "problem": "required"}])
    note = await Note.objects.create(owner=request.user, title=title, body=data.get("body", ""))
    await note.tags.set(await tags_named(data.get("tags", [])))
    await note.fetch_related("tags")
    return Response(await note_json(note), status=201)


@app.get("/notes/{id}")
@login_required
async def get_note(request, id: int):
    note = await Note.objects.get_or_404(pk=id, owner=request.user)
    await note.fetch_related("tags")
    return await note_json(note)


@app.patch("/notes/{id}")
@login_required
async def update_note(request, id: int):
    note = await Note.objects.get_or_404(pk=id, owner=request.user)
    data = await request.json()
    for field in ("title", "body", "pinned"):
        if field in data:
            setattr(note, field, data[field])
    await note.save()
    if "tags" in data:
        await note.tags.set(await tags_named(data["tags"]))
    await note.fetch_related("tags")
    return await note_json(note)


@app.delete("/notes/{id}")
@login_required
async def delete_note(request, id: int):
    note = await Note.objects.get_or_404(pk=id, owner=request.user)
    await note.delete()
```

What happens here:

- **Parameters by name.** `id: int` comes from the path; `q`, `pinned`, `page` from the query
  string, converted by their type hints. `/notes?page=x` gets a `422` that lists the problem.
- **Return values.** A dict or list becomes JSON; `None` becomes `204 No Content`;
  `Response(..., status=201)` sets the status.
- **Errors.** `HTTPException(409, ...)`, `ValidationError([...])` (422), and `get_or_404` all
  produce Ferro's JSON error envelope with a request id.
- **Users.** `create_user` hashes the password with Argon2id and rejects weak ones with 422.
  `auth.login` starts a server-side session (an `HttpOnly` cookie). `@login_required` answers
  401 to anonymous requests.
- **Ownership.** Every query filters on `owner=request.user`, so one user can never read
  another's notes: they get 404, not 403, which also hides that the note exists.
- **No hidden queries.** `prefetch_related("tags")` loads the tags of all listed notes in one
  extra query; reading a relation that was not loaded raises an error instead of quietly
  querying.
- **Limits.** Every client may make 120 requests a minute; registering and logging in only 5.

## 6. Try it

```bash
ferro dev
```

In another terminal (`curl` keeps the session cookie in `jar.txt`):

```bash
curl -c jar.txt -H "content-type: application/json" -d '{"username":"ann","password":"correct horse"}' http://127.0.0.1:8000/register
```

```bash
curl -b jar.txt -H "content-type: application/json" -d '{"title":"Groceries","tags":["home"]}' http://127.0.0.1:8000/notes
```

```bash
curl -b jar.txt "http://127.0.0.1:8000/notes?q=groc"
```

Open <http://127.0.0.1:8000/admin/> and log in as `admin` to browse notes, tags and users.

`ferro dev` reloads when you save a `.py` file and shows tracebacks in error responses;
`ferro run` (production) never does.

Explore data interactively:

```bash
ferro shell
```

```python
>>> await Note.objects.filter(pinned=True).count()
>>> await Tag.objects.values_list("name", flat=True)
```

## 7. Tests

Replace `tests/test_app.py` with `tests/test_notes.py`:

```python
from ferro.testing import TestClient

from app import app

ANN = {"username": "ann", "password": "correct horse"}
BOB = {"username": "bob", "password": "battery staple"}


def test_notes_belong_to_their_owner():
    with TestClient(app, database_url="sqlite:///:memory:") as client:
        assert client.get("/notes").status_code == 401
        assert client.post("/register", json=ANN).status_code == 200

        created = client.post("/notes", json={"title": "Groceries", "tags": ["home"]})
        assert created.status_code == 201
        note = created.json()
        assert note["tags"] == ["home"]

        assert client.post("/notes", json={"title": ""}).status_code == 422
        assert [n["title"] for n in client.get("/notes?q=groc").json()] == ["Groceries"]
        pinned = client.patch(f"/notes/{note['id']}", json={"pinned": True})
        assert pinned.json()["pinned"] is True

        client.post("/logout")
        client.post("/register", json=BOB)
        assert client.get("/notes").json() == []
        assert client.get(f"/notes/{note['id']}").status_code == 404

        client.post("/login", json=ANN)
        assert client.delete(f"/notes/{note['id']}").status_code == 204
        assert client.get("/notes").json() == []


def test_usernames_are_unique():
    with TestClient(app, database_url="sqlite:///:memory:") as client:
        assert client.post("/register", json=ANN).status_code == 200
        assert client.post("/register", json=ANN).status_code == 409
        weak = client.post("/register", json={"username": "cid", "password": "123"})
        assert weak.status_code == 422
```

```bash
python -m pytest
```

`TestClient` starts your app on a real local port with a fresh in-memory database (tables
created automatically), keeps cookies between requests, and stops it at the end of the `with`
block. More in [Testing](10-testing.md).

## 8. Change a model

Add a field to `Note`:

```python
    color = models.String(max_length=20, default="yellow")
```

```bash
ferro makemigrations
```

```bash
ferro migrate
```

The new migration adds the column and fills existing rows with `"yellow"`. Commit migration
files with your code; production runs `ferro migrate` before the new version starts. See
[Migrations](05-migrations.md).

## 9. Run it like production

```bash
ferro run --processes 2
```

Production mode: no auto-reload, no tracebacks in responses, `Secure` cookies, probes at
`/healthz` and `/readyz`. Behind HTTPS on a real server, follow the
[deployment guide](../deployment.md); to size processes and machines, read
[performance and scaling](12-performance-scaling.md).

## Where next

- [Routing, requests and responses](03-routing-requests-responses.md)
- [Models and queries](04-models-and-queries.md)
- [Security](09-security.md) before going live
