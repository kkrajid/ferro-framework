# Quickstart & Validation: Authentication

```python
# app.py
from ferro import App, HTTPException
from ferro import auth
from ferro.auth import authenticate, login_required

app = App(database_url="sqlite:///site.db")
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

```bash
ferro makemigrations && ferro migrate
FERRO_SUPERUSER_PASSWORD="a-long-secret" ferro createsuperuser --username admin --email a@x.io
ferro dev
curl -c jar -X POST -H "content-type: application/json" \
     -d '{"username":"admin","password":"a-long-secret"}' http://127.0.0.1:8000/login
curl -b jar http://127.0.0.1:8000/me        # {"username":"admin"}
```

Automated: `pytest tests/python/auth tests/integration/test_auth_cli.py`.
