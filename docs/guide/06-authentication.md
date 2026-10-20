# Authentication and permissions

`ferro.auth` provides users, groups, permissions, Argon2id password hashing and server-side
sessions. Turn it on with one call:

```python
from ferro import App

app = App()
app.add_auth()          # request.user and request.session on every routed request
# app.add_admin() also calls add_auth()
```

Create the tables with your migrations (`ferro makemigrations && ferro migrate`): the user,
group, permission and session tables are included automatically, and `ferro migrate` creates the
add/change/delete/view permissions of every model.

## Users

```python
from ferro.auth import User

user = await User.objects.create_user("ann", password="correct horse", email="ann@example.com")
admin = await User.objects.create_superuser("admin", "a-long-secret")

await user.set_password("new secret phrase")   # validates and hashes; then:
await user.save()
await user.check_password("new secret phrase")  # True
user.to_dict()                                  # without the password hash
```

Fields: `username` (unique), `email`, `password` (hash), `is_active`, `is_staff`,
`is_superuser`, `date_joined`, `last_login`, `groups`, `user_permissions`.

From the command line:

```bash
ferro createsuperuser --username admin --email admin@example.com
```

(`FERRO_SUPERUSER_PASSWORD=... ferro createsuperuser --username admin --no-input` in scripts.)

**Passwords** are hashed with Argon2id (19 MiB memory, 2 iterations) in Rust on a separate
thread pool, so hashing never blocks other requests. Weak passwords — fewer than 8 characters,
all digits, or the username — raise `PasswordError` (an `HTTPException` with status 422, so an
unhandled one answers the client). Check a password yourself with
`ferro.auth.validate_password(password, username)`.

## Logging in and out

```python
from ferro import HTTPException, auth
from ferro.auth import authenticate


@app.post("/login", rate_limit="5/minute")
async def sign_in(request):
    data = await request.json()
    user = await authenticate(data.get("username", ""), data.get("password", ""), request)
    if user is None:                                  # unknown user, wrong password or inactive
        raise HTTPException(401, "Wrong username or password")
    await auth.login(request, user)
    return {"username": user.username}


@app.post("/logout")
async def sign_out(request):
    await auth.logout(request)
```

- `authenticate` takes as long for an unknown username as for a wrong password, so response
  times do not reveal which accounts exist. Old hashes are upgraded on a successful login.
- `login` starts a new session with a new key (the old key stops working: session fixation
  protection) and keeps the visitor's session data.
- Always rate-limit login routes (`rate_limit="5/minute"`).
- **Failed logins are throttled**: after 5 failures for a username (from one client address,
  when you pass `request`) within 15 minutes, `authenticate` raises `LoginThrottled` — answered
  `429` with `Retry-After` unless you catch it — even for the right password, until the 15
  minutes pass. Unknown usernames count the same way. A successful login resets the count.
  Change it with `app.add_auth(max_login_attempts=10, login_lockout=600)`
  (`max_login_attempts=0` turns it off). Counts live in the [cache](08-cache.md): use the
  database backend with `--processes` so all processes share them. Without `request`, the count
  is per username only, which lets anyone lock a known username out for the lockout period.

## request.user and request.session

```python
request.user.is_authenticated        # False for AnonymousUser
request.user.username
request.session["cart"] = [1, 2]     # any JSON-compatible data, saved after the response
request.session.get("cart", [])
```

How sessions work:

- The `sessionid` cookie holds a random 256-bit key; the database (`auth_session`) stores only
  its SHA-256 digest, so a database leak does not leak usable sessions.
- Cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` outside `ferro dev`.
- Sessions last 14 days and are renewed while used; a login ends after 30 days regardless, and
  when the user's password changes. After a user changes **their own** password, call
  `await auth.login(request, user)` again to keep them signed in.
- Anonymous visitors get a stored session only once you put something in `request.session`.
- Expired sessions are deleted whenever someone logs in.
- An authenticated request costs one database query (session and user together).

## Protecting handlers

```python
from ferro.auth import login_required, permission_required, staff_required


@app.get("/me")
@login_required                          # 401 for anonymous users
async def me(request):
    return {"username": request.user.username}


@app.get("/reports")
@staff_required                          # 401 anonymous, 403 non-staff
async def reports(request): ...


@app.put("/products/{id}")
@permission_required("change_product")   # 401 anonymous, 403 without the permission
async def update_product(request, id: int): ...
```

The decorators go **below** the route decorator, work on `async def` handlers, and need the
handler to declare a `request` parameter (both checked at startup).

In code: `await request.user.has_perm("change_product")`, `await user.has_perms([...])`.

## Permissions and groups

Permission codenames are `<action>_<table>`: `add_product`, `change_product`,
`delete_product`, `view_product` for a model whose table is `product`. `ferro migrate`
creates them; `await ferro.auth.create_permissions([Model])` does it in code.

```python
from ferro.auth import Group, Permission

editors = await Group.objects.create(name="editors")
await editors.permissions.add(*await Permission.objects.filter(codename__in=["change_product", "view_product"]))
await user.groups.add(editors)
await user.user_permissions.add(await Permission.objects.get(codename="delete_product"))
```

Active superusers have every permission; inactive users have none. Your own permissions are
just rows: `await Permission.objects.create(codename="export_reports", name="Can export reports")`.

## API clients (tokens)

Sessions suit browsers. For machine clients, keep a key table and check it in middleware:

```python
import hashlib, secrets
from ferro import Response, models
from ferro.auth import User


class ApiToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="api_tokens")
    digest = models.String(max_length=64, unique=True)       # store a hash, never the token


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_token(user: User) -> str:
    token = secrets.token_urlsafe(32)
    await ApiToken.objects.create(user=user, digest=digest(token))
    return token                                              # show it once


@app.middleware
async def bearer_tokens(request, call_next):
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        found = await ApiToken.objects.select_related("user").filter(
            digest=digest(header[7:])
        ).first()
        if found is None or not found.user.is_active:
            return Response({"error": {"status": 401, "message": "Invalid token"}}, status=401)
        request.state.api_user = found.user
    return await call_next(request)
```

Requests without cookies are not subject to the cross-origin (CSRF) check, so token clients work
from any origin once [CORS](09-security.md#cors) allows them.
