# Feature Specification: Authentication

**Feature Branch**: `007-authentication`

**Created**: 2026-10-11

**Status**: Draft

**Input**: User description: "Feature 007 from docs/roadmap.md (vision Phase 6): user model, password
hashing, sessions, login/logout, permissions, groups — Django-like."

## Clarifications

### Session 2026-10-11

The user asked to continue without interruptions; recommended options were taken and recorded.

- Q: How are passwords hashed? → A: Argon2id (OWASP parameters: 19 MiB memory, 2 iterations,
  1 lane) in the native core, off the event loop, stored as a standard PHC string so parameters
  can be raised later; verification is constant-time and hashes with old parameters are
  upgraded on the next successful login.
- Q: How do sessions work? → A: Server-side sessions in a database table; the browser gets a
  random key in an `HttpOnly`, `SameSite=Lax` (and `Secure` in production) cookie; only a SHA-256
  digest of the key is stored, so a database leak does not reveal live sessions. Logging in
  rotates the key. Default lifetime 14 days, sliding.
- Q: How is the current user available? → A: `app.add_auth()` installs the auth middleware;
  `request.user` is then always set — a `User` or an `AnonymousUser` — with one query per request
  for logged-in users (session joined with its user).
- Q: What are permission names? → A: `"<action>_<table>"` strings (`"add_product"`,
  `"view_order"`), created by `ferro.auth.create_permissions()` (called by `migrate`) for every
  model with the actions add, change, delete, view; custom ones can be added. Superusers have all
  permissions; inactive users none.
- Q: Is `createsuperuser` part of this feature? → A: Yes, as `ferro createsuperuser` (the project
  generator and other commands remain Feature 009), because the admin (Feature 008) needs it.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Users and passwords (Priority: P1)

```python
from ferro.auth import User

user = await User.objects.create_user("ann", password="correct horse battery", email="a@x.io")
await user.check_password("correct horse battery")   # True
await authenticate("ann", "wrong")                    # None
```

**Why this priority**: Every login-based application starts with users and safe passwords.

**Acceptance Scenarios**:

1. **Given** `create_user`, **Then** the password is stored only as an Argon2id hash; `create_superuser`
   sets staff and superuser flags.
2. **Given** `authenticate(username, password)`, **Then** the user is returned for the right
   password of an active user and `None` otherwise, taking about the same time whether or not the
   user exists.
3. **Given** a password shorter than 8 characters, entirely numeric, or equal to the username,
   **Then** `set_password` / `create_user` raise `ValidationError` listing the problems.
4. **Given** hashing, **Then** it does not block other requests (runs off the event loop).

---

### User Story 2 - Sessions, login and logout (Priority: P1)

```python
app = App()
app.add_auth()

@app.post("/login", rate_limit="5/minute")
async def login(request):
    data = await request.json()
    user = await authenticate(data["username"], data["password"])
    if user is None:
        raise HTTPException(401, "Invalid credentials")
    await auth.login(request, user)
    return {"ok": True}

@app.get("/me")
@login_required
async def me(request):
    return {"username": request.user.username}
```

**Why this priority**: Browser applications and the admin need a logged-in user across requests.

**Acceptance Scenarios**:

1. **Given** a successful `login`, **Then** the response sets the session cookie and later requests
   with it have `request.user` set to the user; `last_login` is updated.
2. **Given** no cookie, an unknown, expired or tampered cookie, **Then** `request.user` is an
   `AnonymousUser` (`is_authenticated` false).
3. **Given** `logout`, **Then** the session row is deleted and the cookie cleared.
4. **Given** `login` while a session exists, **Then** the session key changes (fixation defence).
5. **Given** `request.session["cart"] = [...]`, **Then** the data persists for that session.
6. **Given** a deactivated user, **Then** their sessions no longer authenticate.

---

### User Story 3 - Permissions and groups (Priority: P2)

```python
editors = await Group.objects.create(name="editors")
await editors.permissions.add(await Permission.objects.get(codename="change_product"))
await user.groups.add(editors)
await user.has_perm("change_product")   # True

@app.put("/products/{id}")
@permission_required("change_product")
async def update(request, id: int): ...
```

**Acceptance Scenarios**:

1. **Given** `create_permissions()` (run by `ferro migrate`), **Then** add/change/delete/view
   permissions exist for every model, once.
2. **Given** direct or group permissions, **Then** `has_perm` / `has_perms` answer correctly;
   superusers have every permission; inactive users none.
3. **Given** `@login_required`, **Then** anonymous requests get 401; **Given**
   `@permission_required(...)` / `@staff_required`, **Then** missing permissions give 403.

---

### User Story 4 - createsuperuser and migrations (Priority: P2)

**Acceptance Scenarios**:

1. **Given** a project importing `ferro.auth`, **When** `ferro makemigrations`, **Then** the auth
   tables are included; `ferro migrate` creates them and the default permissions.
2. **Given** `ferro createsuperuser --username admin --email a@x.io` with the password from a prompt
   or `FERRO_SUPERUSER_PASSWORD`, **Then** a superuser exists; weak passwords are refused.

### Edge Cases

- Session cookie present but the auth tables are missing: clear error naming `ferro migrate`.
- Many concurrent logins: hashing runs on a bounded pool; the event loop stays responsive.
- Password hashes with unknown algorithms (imported users): `check_password` returns false.
- Usernames are case-sensitive but stripped; email optional.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Built-in models `User`, `Group`, `Permission`, `Session` in `ferro.auth`, registered
  when `ferro.auth` is imported.
- **FR-002**: Argon2id password hashing and constant-time verification in the native core, run off
  the event loop; PHC strings; rehash on login when parameters changed.
- **FR-003**: `authenticate`, `login`, `logout`, `request.user`, `request.session`,
  `AnonymousUser`.
- **FR-004**: Database sessions with hashed keys, rotation on login, sliding expiry, secure cookie
  defaults (Feature 006).
- **FR-005**: Permissions (`<action>_<table>`), groups, `has_perm(s)`, superuser/active rules,
  `create_permissions()`.
- **FR-006**: `login_required`, `permission_required`, `staff_required` decorators returning the
  standard 401/403 error envelopes.
- **FR-007**: `ferro createsuperuser` with password validation.
- **FR-008**: Everything works on SQLite and PostgreSQL.

## Success Criteria *(mandatory)*

- **SC-001**: Every acceptance scenario passes on both databases.
- **SC-002**: While 20 logins hash passwords concurrently, a cheap endpoint's p99 latency stays
  below 50 ms.
- **SC-003**: An authenticated request costs one database query for the user and session.
- **SC-004**: `authenticate` for an unknown user and for a wrong password take the same time
  within 20%.

## Assumptions

- Login forms/templates, password reset e-mails, OAuth and 2FA are out of scope.
- Custom user models are out of scope for the MVP (extra fields go in a related profile model).
