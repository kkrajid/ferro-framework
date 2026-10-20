"""Authentication: users, groups, permissions, database sessions.

```python
from ferro import App, auth
from ferro.auth import authenticate, login_required

app = App()
app.add_auth()          # request.user / request.session on every routed request

@app.post("/login")
async def sign_in(request):
    data = await request.json()
    user = await authenticate(data["username"], data["password"])
    ...
    await auth.login(request, user)
```

Importing ``ferro.auth`` registers its models, so ``ferro makemigrations`` creates the tables.
"""

from ferro.auth.hashers import PasswordError, make_password, validate_password
from ferro.auth.models import AnonymousUser, Group, Permission, Session, User
from ferro.auth.permissions import (
    create_permissions,
    login_required,
    permission_required,
    staff_required,
)
from ferro.auth.sessions import LoginThrottled, auth_middleware, authenticate, login, logout

__all__ = [
    "AnonymousUser",
    "Group",
    "LoginThrottled",
    "PasswordError",
    "Permission",
    "Session",
    "User",
    "auth_middleware",
    "authenticate",
    "create_permissions",
    "login",
    "login_required",
    "logout",
    "make_password",
    "permission_required",
    "staff_required",
    "validate_password",
]
