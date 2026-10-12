# The admin

A back office for your data, generated from the models: lists with search, filters and
ordering; add, change and delete forms; per-model permissions.

```python
# admin.py
from ferro.admin import ModelAdmin, admin
from models import Category, Product

admin.register(Category)
admin.register(
    Product,
    ModelAdmin(
        list_display=["name", "price", "category", "active"],   # columns ("__str__" allowed)
        search_fields=["name", "category__name"],               # case-insensitive, OR-ed
        list_filter=["category", "active"],                     # filter links in the sidebar
        ordering=["-price"],
        list_per_page=50,
        readonly_fields=["created"],
    ),
)
```

```python
# app.py
import admin  # noqa: F401 - registers the models
from ferro import App

app = App()
app.add_admin()                 # mounted at /admin; also turns on authentication
```

`add_admin(site=None, prefix="/admin")`: pass another `AdminSite()` or prefix if needed. Names in
`ModelAdmin` options are checked when the app starts.

Create a staff account and open <http://127.0.0.1:8000/admin/>:

```bash
ferro createsuperuser --username admin
```

## Who can do what

- Only **active staff** users can log in (`/admin/login`, limited to 10 attempts a minute per
  client).
- **Superusers** see and change everything.
- Other staff need the model permissions: `view_<table>` to see a model, `add_<table>`,
  `change_<table>`, `delete_<table>` for the rest. Assign them to users or groups (in the admin
  itself once `User` and `Group` are registered).
- Every check runs on the server for every page and form; hiding a link is never the only
  protection.

## Forms

Generated from the field types: text inputs with length limits, numbers, dates and times,
checkboxes, JSON text areas, select lists for foreign keys and multi-selects for many-to-many
(a key input when a table has more than 500 rows). Validation messages appear next to the
fields; unique violations name the field. `readonly_fields` are shown but never accepted from
the form.

Deleting shows what else will be deleted (`CASCADE`) or why it cannot be (`PROTECT`) before
asking for confirmation.

## Managing users in the admin

```python
from ferro.auth import Group, User

admin.register(User, ModelAdmin(list_display=["username", "email", "is_staff", "is_active"],
                                search_fields=["username", "email"]))
admin.register(Group)
```

The user form never shows password hashes: the password field is write-only, hashed with
`set_password` (weak passwords are refused) and left unchanged when empty. Only superusers can
change `is_superuser`, groups and permissions, so staff with `change_auth_user` cannot promote
themselves.

Many-to-many relations through your own link model are read-only on the form; register the link
model to edit its rows.

## Security

Pages are plain HTML forms without JavaScript; every value is escaped, pages carry a strict
Content-Security-Policy and `cache-control: no-store`, forms are protected by the cross-origin
check, and logout is a POST. Put the admin behind HTTPS (cookies are `Secure` in production).
